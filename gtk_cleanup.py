#!/usr/bin/env python3
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

@todo: Rewrite docs for Sphinx
@todo: Finish refactoring away the complexity that was present because GTK+ 2.x
       made GtkRecentManager one-per-display instead of global.
@todo: Look for ways to refactor this so it's less "2014 OOP-heavy me"
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

# Prevent Python 2.x PyLint from complaining if run on this
from __future__ import (absolute_import, division, print_function,
                        with_statement, unicode_literals)

__author__ = "Stephan Sokolow (deitarion/SSokolow)"
__appname__ = "GTK+ Recent Files Scrubber"
__version__ = "0.2"
__license__ = "GNU GPL 3.0 or later"


import hashlib, logging, os, sys, urllib.request
log = logging.getLogger(__name__)  # pylint: disable=C0103


XDG_DATA_DIR = os.environ.get('XDG_DATA_HOME',
        os.path.expanduser('~/.local/share'))

import gi  # type: ignore
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk, GLib  # type: ignore

from typing import Dict, List, Tuple


class Blacklist(object):
    def __init__(self, filename: str=None):
        self.filename = os.path.join(XDG_DATA_DIR, filename or 'grms.conf')
        self._contents = []  # type: List[Tuple[str, int]]

    def __contains__(self, uri: str) -> bool:
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

    def _hash_prefix(self, prefix: str, limit: int=None) -> Tuple[str, int]:
        """Single location for hash-generation code."""
        prefix = prefix[:limit]
        # TODO: Set up to transparently migrate from SHA-1 to something better
        return hashlib.sha1(prefix.encode('utf8')).hexdigest(), len(prefix)

    def add(self, prefix: str):
        """Add a prefix to the hashed blacklist.
        @todo: Look into a more efficient way to maintain a sorted list.
        """
        if prefix in self:
            log.debug("Prefix already in the blacklist: %s", prefix)
        else:
            self._contents.append(self._hash_prefix(prefix))
            self._contents.sort(key=lambda x: x[1])

    def empty(self):
        self._contents = []

    def index(self, uri: str) -> int:
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

    def load(self) -> bool:
        """@todo: Look into using something to insert() sort instead."""
        if not os.path.exists(self.filename):
            log.debug("No blacklist found: %s", self.filename)
            return False

        try:
            seen = []  # type: List[str]

            with open(self.filename, 'r') as fobj:
                results = []
                for row in fobj:
                    row = row.strip()
                    if row.startswith('#') or not row:
                        continue

                    digest, count = row.strip().split(None, 2)
                    if digest not in seen:  # Catch and remove duplicates
                        key = (digest, int(count))
                        seen.append("%s,%d" % key)
                        results.append(key)

            results.sort(key=lambda x: x[1])
            self._contents = results

            return True
        except ValueError as err:
            log.error("Invalid blacklist format for %s:\n\t%s",
                      self.filename, err)
            return False

    def remove(self, uri: str):
        """Remove the first prefix match for C{uri} from the blacklist.

        @raises ValueError: No prefixes matched the given URI.
        """
        try:
            self._contents.pop(self.index(uri))
        except IndexError:
            raise ValueError("%s doesn't match any prefixes in the list" % uri)

    def remove_all(self, uri: str):
        """Remove all prefixes from the blacklist which match C{uri}."""
        try:
            while True:
                self.remove(uri)
        except ValueError:
            pass

    def save(self) -> bool:
        """
        @note: Blacklists are stored reversed on disk so that, if they are
            provided sorted for use, the on-disk format will resemble an
            append-friendly list of hashes of frequently-viewed files.
        """
        try:
            with open(self.filename, 'w') as fobj:
                for row in sorted(self._contents,
                        key=lambda x: x[1], reverse=True):
                    fobj.write('%s\t%d\n' % row)
            return True
        except Exception as err:
            log.error("Failed to write to %s: %s", self.filename, err)
            return False


class RecentManagerScrubber(object):
    def __init__(self, blacklist: Blacklist):
        self.blacklist = blacklist
        self.watched_files = {}  # type: Dict[str, Gtk.RecentManager]
        self.attached = False

    def attach(self):  # pylint: disable=W0613
        """Call L{scrub_entries} on all screens and attach it as a change
        listener.
        """
        manager = Gtk.RecentManager.get_default()

        manager_fname = manager.props.filename
        if manager_fname in self.watched_files:
            log.debug("Already watched. Skipping: %s", manager_fname)
            return
        else:
            log.debug("Watching recent files store: %s", manager_fname)
            self.scrub(manager)
            manager.connect('changed', self.scrub)
            self.watched_files[manager_fname] = manager

        if not os.stat(manager_fname).st_mode & 0o777 == 0o600:
            log.warning("Bad file permissions on recent list. Fixing: %s",
                    manager_fname)
            try:
                os.chmod(manager_fname, 0o600)
            except OSError:
                log.error("Failed to chmod %s", manager_fname)

    def purge(self):
        """Purge all entries from attached Recently Used lists."""
        if not self.attached:
            # TODO: Better exception.
            raise Exception("No managers attached. Cannot purge.")

        for fname, manager in self.watched_files.items():
            try:
                manager.purge_items()
                log.info("Purged %s", fname)
            except GLib.Error as err:
                log.error("Error while purging %s: %s", fname, err)

    def start(self):
        """Scrub all lists we can find and watch for changes."""
        # Make sure we don't double-connect our signals
        if self.attached:
            log.debug("RecentManagerScrubber already started. Skipping.")
            return

        self.attach()
        self.attached = True

    def scrub(self, recent_manager: Gtk.RecentManager):
        """Given a Gtk.RecentManager, remove all entries in the blacklist."""
        found = []
        for item in recent_manager.get_items():
            uri = item.get_uri()
            if uri in self.blacklist:
                found.append(uri)
            else:
                log.debug('Skipped %s', item.get_display_name())

        # Remove found entries in one batch so we can show a summarized message
        # (Keeps potential log files clean and avoids leaking data into them)
        if found:
            log.info("Removing %d entries", len(found))
            while found:
                try:
                    recent_manager.remove_item(found.pop())
                except GLib.Error:
                    log.warning("Failed to remove item. (Maybe already done)")


def main():
    """The main entry point, compatible with setuptools entry points."""
    from argparse import ArgumentParser, RawDescriptionHelpFormatter
    parser = ArgumentParser(formatter_class=RawDescriptionHelpFormatter,
        description=__doc__.replace('\r\n', '\n').split('\n--snip--\n')[0])
    parser.add_argument('--version', action='version',
        version="%%(prog)s v%s" % __version__)
    parser.add_argument('-v', '--verbose', action="count",
        default=2, help="Increase the verbosity. Use twice for extra effect.")
    parser.add_argument('-q', '--quiet', action="count",
        default=0, help="Decrease the verbosity. Use twice for extra effect.")
    # Reminder: %(default)s can be used in help strings.

    resopt = parser.add_argument_group("Resident-Compatible Actions")
    resopt.add_argument('--purge', action="store_true", default=False,
        help="Purge all Recently Used entries during the initial scrub.")
    resopt.add_argument('--config', action="store", default=None,
        help="Specify a non-default config file", metavar="FILE")

    nonres = parser.add_argument_group("Non-Resident Actions")
    nonres.add_argument('-a', '--add', action="append", dest="additions",
        help="Add URI to the list of blacklisted prefixes.",
        default=[], metavar="URI")
    nonres.add_argument('-r', '--remove', action="append", dest="removals",
        help="Remove prefixes from the blacklist which match URI",
        default=[], metavar="URI")
    nonres.add_argument('--once', action="store_true", default=False,
        help="Don't become resident. Just scrub and exit.")

    args = parser.parse_args()

    # Set up clean logging to stderr
    log_levels = [logging.CRITICAL, logging.ERROR, logging.WARNING,
              logging.INFO, logging.DEBUG]
    args.verbose = min(args.verbose - args.quiet, len(log_levels) - 1)
    args.verbose = max(args.verbose, 0)
    logging.basicConfig(level=log_levels[args.verbose],
                format='%(levelname)s: %(message)s')

    # Prepare the blacklist
    blist = Blacklist(args.config)
    blist.load()

    if args.additions or args.removals:
        for uri in args.additions:
            if (uri[0] == os.sep or uri[0] == os.altsep or os.path.exists(uri)
                    or os.path.exists(os.path.split(uri)[0])):
                uri = 'file://' + urllib.request.pathname2url(
                    os.path.abspath(uri))
            blist.add(uri)
        for uri in args.removals:
            blist.remove_all(uri)
        blist.save()
        sys.exit(0)

    # Scrub and start watching all accessible X11 displays
    scrubber = RecentManagerScrubber(blist)
    scrubber.start()

    if args.purge:
        scrubber.purge()

    if not args.once:
        Gtk.main()

if __name__ == '__main__':
    main()

# vim: set sw=4 sts=4 expandtab :
