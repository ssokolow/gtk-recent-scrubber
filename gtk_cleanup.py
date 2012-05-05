#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""A simple little tool which watches GTK+'s global recent files list and
removes anything that matches a hashed blacklist of URI prefixes.

--snip--

@note: The config file is stored in XDG_DATA_DIR for two reasons:
    1. It's probably a good idea to have it roaming.
    2. It's more likely to be mistaken for a mapping between
       content hashes and something like visit counts.

@note: For now, the liberal use of sort() calls is acceptable because
    Timsort takes advantage of sorted subsequences and I don't expect these
    lists to get very long.

    (And if it does, a list of 20 entries can be sorted a million times over
    in under 3 seconds on my system and reverse=True'd in under 5 seconds.)

@todo: Implement a test suite with full coverage.
@todo: Audit for uncaught exceptions.
@todo: Audit and improve docstrings.
@todo: Write a C{setup.py} and an C{autostart/gtk_cleanup.desktop} for
       potential packagers.

@todo: Performance optimizations to look into:
    - Explore optimizations to reduce the work done (hashing, etc.) for each
      entry that hasn't been added since our last scrub.
    - Find a way to avoid running the scrubber in response to the changes
      it itself made. (Ideally, a general way to stop a callback from running
      on events it itself emitted without the risk of race conditions.)

@todo: Find a future-proof way to offer an option to set the private hint on
    existing recent entries as an alternative to deleting them.
    (So they only show up in the application which added them)

@todo: Design a GUI equivalent to Chrome's view for deleting individual
    history entries.
"""

__appname__ = "GTK+ RecentManager Scrubber"
__author__  = "Stephan Sokolow (deitarion/SSokolow)"
__version__ = "0.1"
__license__ = "GNU GPL 3.0 or later"


import hashlib, logging, os, sys, urllib
log = logging.getLogger(__name__)

XDG_DATA_DIR = os.environ.get('XDG_DATA_HOME',
        os.path.expanduser('~/.local/share'))

try:
    import pygtk
    pygtk.require("2.0")
except ImportError:
    pass

import gtk, gobject

class Blacklist(object):
    def __init__(self, filename=None):
        self.filename = os.path.join(XDG_DATA_DIR, filename or 'grms.conf')
        self._contents = []

    def __contains__(self, uri):
        """Custom 'in' operator behaviour for a list of prefix hashes.

        (Tuple ordering chosen because, in an undocumented data file, it is
        more likely to be dismissed as something like a mapping between hashes
        of image content and counts of how many times you viewed them.)
        """
        try:
            self.index(uri)
            return True
        except IndexError:
            return False

    def _hash_prefix(self, prefix, limit=None):
        """Single location for hash-generation code."""
        prefix = prefix[:limit]
        return hashlib.sha1(prefix).hexdigest(), len(prefix)

    def add(self, prefix):
        """Add a prefix to the hashed blacklist.
        @todo: Look into using something to insert() sort instead.
        """
        if prefix in self:
            log.debug("Prefix already in the blacklist: %s" % prefix)
        else:
            self._contents.append(self._hash_prefix(prefix))
            self._contents.sort(key=lambda x: x[1])

    def empty(self):
        self._contents = []

    def index(self, uri):
        """Custom 'index' method to do prefix matching."""
        uri_len = len(uri)
        for pos, val in enumerate(self._contents):
            prefix_hash, prefix_len = val
            if prefix_len > uri_len:
                # Skip things that can't possibly match
                break
            if prefix_hash == self._hash_prefix(uri, prefix_len)[0]:
                return pos
        raise IndexError("%s does not match any prefixes in the list" % uri)

    def load(self):
        """@todo: Look into using something to insert() sort instead."""
        if not os.path.exists(self.filename):
            log.debug("No blacklist found: %s", self.filename)
            return False

        try:
            seen = []

            with open(self.filename, 'rU') as fh:
                results = []
                for row in fh:
                    row = row.strip()
                    if row.startswith('#') or not row:
                        continue

                    digest, count = row.strip().split(None, 2)
                    if digest not in seen: # Catch and remove duplicates
                        key = (digest, int(count))
                        seen.append("%s,%d" % key)
                        results.append(key)

            results.sort(key=lambda x: x[1])
            self._contents = results

            return True
        except ValueError, err:
            log.error("Invalid blacklist format for %s:\n\t%s", self.filename, err)
            return False

    def remove(self, uri):
        """Remove the first prefix match for C{uri} from the blacklist.

        @raises ValueError: No prefixes matched the given URI.
        """
        try:
            self._contents.pop(self.index(uri))
        except IndexError:
            raise ValueError("%s does not match any prefixes in the list" % uri)

    def remove_all(self, uri):
        """Remove all prefixes from the blacklist which match C{uri}."""
        try:
            while True:
                self.remove(uri)
        except ValueError:
            pass

    def save(self):
        """
        @note: Blacklists are stored reversed on disk so that, if they are provided
            sorted for use, the on-disk format will resemble an append-friendly
            list of hashes of frequently-viewed files.
        """
        try:
            with open(self.filename, 'w') as fh:
                for row in sorted(self._contents, key=lambda x: x[1], reverse=True):
                    fh.write('%s\t%d\n' % row)
            return True
        except Exception, err:
            log.error("Failed to write to %s: %s", self.filename, err)
            return False

class RecentManagerScrubber(object):
    def __init__(self, blacklist):
        self.blacklist = blacklist
        self.watched_files = {}
        self.attached = False

    def attach(self, display_manager, display):
        """Given a GdkDisplayManager and a GdkDisplay, call L{scrub_entries} on
        all screens and attach it as a change listener.

        @todo: Do I need to find a way to listen for the addition of new screens
               or is that impossible?

        @todo: When a display emits the "closed" signal, does that display's
               manager also stop listening or is my "only once per filename"
               optimization safe?
        """
        for screen in range(0, display.get_n_screens()):
            manager = gtk.recent_manager_get_for_screen(display.get_screen(screen))

            manager_fname = manager.get_property('filename')
            if manager_fname in self.watched_files:
                log.debug("Already watched. Skipping: %s", manager_fname)
                continue
            else:
                log.debug("Watching recent files store: %s", manager_fname)
                self.scrub(manager)
                manager.connect('changed', self.scrub)
                self.watched_files[manager_fname] = manager

            if not os.stat(manager_fname).st_mode & 0777 == 0600:
                log.warning("Bad file permissions on recent list. Fixing: %s", manager_fname)
                try:
                    os.chmod(manager_fname, 0600)
                except OSError:
                    log.error("Failed to chmod %s", manager_fname)

    def purge(self):
        """Purge all entries from attached Recently Used lists."""
        if not self.attached:
            #TODO: Better exception.
            raise Exception("No managers attached. Cannot purge.")

        for fname, manager in self.watched_files.items():
            try:
                manager.purge_items()
                log.info("Purged %s", fname)
            except gobject.GError, err:
                log.error("Error while purging %s: %s", fname, err)

    def start(self):
        """Scrub all lists we can find and watch for changes."""
        # Make sure we don't double-connect our signals
        if self.attached:
            log.debug("RecentManagerScrubber already started. Skipping.")
            return

        display_manager = gtk.gdk.display_manager_get()
        for display in display_manager.list_displays():
            self.attach(display_manager, display)
            display_manager.connect('display-opened', self.attach)
        self.attached = True

    def scrub(self, recent_manager):
        """Given a GtkRecentManager, remove all entries in the blacklist."""
        found = []
        for x in recent_manager.get_items():
            uri = x.get_uri()
            if uri in self.blacklist:
                found.append(uri)
            else:
                log.debug('Skipped %s', x.get_display_name())

        # Remove all found entries in one batch so we can show a summarized message
        # (Keeps potential log files clean and avoids leaking data into them)
        if found:
            log.info("Removing %d entries" % len(found))
            while found:
                try:
                    recent_manager.remove_item(found.pop())
                except gobject.GError, err:
                    log.warning("Failed to remove item: %s", err)


if __name__ == '__main__':
    from optparse import OptionParser, OptionGroup
    parser = OptionParser(version="%%prog v%s" % __version__,
            usage="%prog [options]",
            description=__doc__.replace('\r\n','\n').split('\n--snip--\n')[0])
    parser.add_option('-v', '--verbose', action="count", dest="verbose",
        default=2, help="Increase the verbosity. Can be used twice for extra effect.")
    parser.add_option('-q', '--quiet', action="count", dest="quiet",
        default=0, help="Decrease the verbosity. Can be used twice for extra effect.")
    #Reminder: %default can be used in help strings.

    resopt = OptionGroup(parser, "Resident-Compatible Actions")
    resopt.add_option('--purge', action="store_true", dest="purge",
        default=False, help="Purge all Recently Used entries during the initial scrub.")
    resopt.add_option('--config', action="store", dest="config",
        default=None, help="Specify a non-default config file", metavar="FILE")
    parser.add_option_group(resopt)

    nonres = OptionGroup(parser, "Non-Resident Actions")
    nonres.add_option('-a', '--add', action="append", dest="additions",
        default=[], metavar="URI", help="Add URI to the list of blacklisted prefixes.")
    nonres.add_option('-r', '--remove', action="append", dest="removals",
        default=[], metavar="URI", help="Remove prefixes from the blacklist which match URI",)
    nonres.add_option('--once', action="store_true", dest="once",
        default=False, help="Don't become resident. Just scrub and exit.")
    parser.add_option_group(nonres)

    # Allow pre-formatted descriptions
    parser.formatter.format_description = lambda description: description

    opts, args  = parser.parse_args()

    # Set up clean logging to stderr
    log_levels = [logging.CRITICAL, logging.ERROR, logging.WARNING,
                  logging.INFO, logging.DEBUG]
    opts.verbose = min(opts.verbose - opts.quiet, len(log_levels) - 1)
    opts.verbose = max(opts.verbose, 0)
    logging.basicConfig(level=log_levels[opts.verbose],
                        format='%(levelname)s: %(message)s')

    # Prepare the blacklist
    blist = Blacklist(opts.config)
    blist.load()

    if opts.additions or opts.removals:
        for x in opts.additions:
            if (x[0] == os.sep or x[0] == os.altsep or os.path.exists(x) or
                    os.path.exists(os.path.split(x)[0])):
                x = 'file://' + urllib.pathname2url(os.path.abspath(x))
            blist.add(x)
        for x in opts.removals:
            blist.remove_all(x)
        blist.save()
        sys.exit(0)

    # Scrub and start watching all accessible X11 displays
    scrubber = RecentManagerScrubber(blist)
    scrubber.start()

    if opts.purge:
        scrubber.purge()

    if not opts.once:
        gtk.main()
