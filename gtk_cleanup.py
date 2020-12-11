#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A simple little tool which watches GTK's global recent files list and
removes anything that matches a hashed blacklist of URI prefixes.

--snip--

NOTE: The config file is stored in XDG_DATA_DIR for two reasons:
    1. It's probably a good idea to have it roaming.
    2. It's more likely to be mistaken for a mapping between
       content hashes and something like visit counts.

NOTE: For now, the liberal use of sort() calls is acceptable because
    Timsort takes advantage of sorted subsequences and I don't expect these
    lists to get very long.

    (And if it does, a list of 20 entries can be sorted a million times over
    in under 3 seconds on my system and reverse=True'd in under 5 seconds.)

TODO: Implement a test suite with full coverage.

TODO: Write a ``setup.py`` and an ``autostart/gtk_cleanup.desktop`` for
       potential packagers.

TODO: Performance optimizations to look into:
      - Explore optimizations to reduce the work done (hashing, etc.) for each
        entry that hasn't been added since our last scrub.
      - Find a way to avoid running the scrubber in response to the changes
        it itself made. (Ideally, a general way to stop a callback from running
        on events it itself emitted without the risk of race conditions.)

TODO: Find a future-proof way to offer an option to set the private hint on
      existing recent entries as an alternative to deleting them.
      (So they only show up in the application which added them)

TODO: Design a GUI equivalent to Chrome's view for deleting individual
      history entries.
"""

# Prevent Python 2.x PyLint from complaining if run on this
from __future__ import (absolute_import, division, print_function,
                        with_statement, unicode_literals)

__author__ = "Stephan Sokolow (deitarion/SSokolow)"
__appname__ = "GTK Recent Files Scrubber"
__version__ = "0.2"
__license__ = "GNU GPL 3.0 or later"


import hashlib, logging, os, sys
from urllib.request import pathname2url

log = logging.getLogger(__name__)  # pylint: disable=C0103


XDG_DATA_DIR = os.environ.get('XDG_DATA_HOME',
        os.path.expanduser('~/.local/share'))

import gi  # type: ignore
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk, GLib  # type: ignore

from typing import Dict, List, Tuple


class Blacklist(object):
    def __init__(self, filename: str):
        self.filename = filename
        self._contents = []  # type: List[Tuple[int, str]]

    @staticmethod
    def _hash_prefix(prefix: str) -> str:
        """Single location for hash-generation code."""
        return hashlib.sha1(prefix.encode('utf8')).hexdigest()

    def add(self, prefix: str):
        """Add a prefix to the hashed blacklist."""
        try:
            self.index(prefix)
            log.debug("Prefix already in the blacklist: %s", prefix)
        except IndexError:
            self._contents.append((len(prefix), self._hash_prefix(prefix)))
            self._contents.sort()  # Keep sorted for short-circuit matching

    def index(self, uri: str) -> int:
        """Find the list index of the first hashed prefix that matches 'uri'.

        Raises IndexError if nothing matches.
        """
        for pos, (prefix_len, prefix_hash) in enumerate(self._contents):
            if prefix_len > len(uri):  # Rely on the list being kept sorted
                break
            if prefix_hash == self._hash_prefix(uri[:prefix_len]):
                return pos
        raise IndexError("%s does not match any prefixes in the list" % uri)

    def load(self) -> bool:
        """Load the blacklist from the associated file

        Raises OSError or a subclass of it if the file cannot be opened.

        (Will replace any unsaved changes in memory)
        """
        if not os.path.exists(self.filename):
            log.debug("No blacklist found: %s", self.filename)
            return False

        try:
            results = set()  # Use a set to collapse duplicates

            # Intentionally die if we don't have permissions for the blacklist
            with open(self.filename, 'r') as fobj:
                # ...but UnicodeDecodeError if it's malformed, since
                # UnicodeDecodeError is subclass of ValueError
                for row in fobj:
                    row = row.strip()  # Allow leading and trailing whitespace

                    if row.startswith('#') or not row:
                        continue  # Allow comments and blank lines in the file

                    # ValueError if field count or second field type are wrong
                    digest, prefix_len = row.split(None, 2)

                    if len(digest) != 40:
                        raise ValueError("Field 0 is not a valid MD5sum")
                    results.add((int(prefix_len), digest))

            # Replace atomically and keep sorted for short-circuit matching
            self._contents = list(sorted(results))
            return True
        except ValueError as err:
            log.error("Malformed blacklist (%s):\n\t%s", self.filename, err)
            return False

    def remove_all(self, uri: str):
        """Remove all prefixes from the blacklist which match ``uri``."""
        while True:
            try:
                self._contents.pop(self.index(uri))
            except IndexError:
                break

    def save(self):
        """Save any changes to the blacklist to disk.

        Raises OSError or a subclass of it if the file cannot be opened.

        NOTE: Blacklists are stored with the fields reversed on disk and in
              reversed order so that the on-disk format will resemble some kind
              of MRU list based on the hashes of file contents.
        """
        # Intentionally die if we don't have permissions for the blacklist
        with open(self.filename, 'w') as fobj:
            for prefix_len, digest in sorted(self._contents, reverse=True):
                fobj.write('%s\t%d\n' % (digest, prefix_len))


class RecentManagerScrubber(object):
    def __init__(self, blacklist: Blacklist):
        self.blacklist = blacklist
        self.manager = Gtk.RecentManager.get_default()
        self.handler = None

    def purge(self):
        """Purge all entries from attached Recently Used lists."""
        path = self.manager.props.filename
        try:
            count = self.manager.purge_items()
            log.info("Purged %d items from %s", count, path)
        except GLib.Error as err:
            log.error("Could not purge %s: %s", path, err)

    def scrub(self, recent_manager: Gtk.RecentManager):
        """Given a Gtk.RecentManager, remove all entries in the blacklist."""
        log.debug("Cleaning MRU list...")
        found = []
        for item in recent_manager.get_items():
            uri = item.get_uri()
            try:
                self.blacklist.index(uri)
                found.append(uri)
            except IndexError:
                log.debug('Skipped %s', item.get_display_name())

        if not found:
            return

        # Remove found entries in one batch so we can show a summarized message
        # (Keeps potential log files clean and avoids leaking data into them)
        log.info("Removing %d entries", len(found))
        while found:
            try:
                recent_manager.remove_item(found.pop())
            except GLib.Error:
                log.warning("Failed to remove item. (Maybe already done)")

    def start(self):
        """Scrub the MRU list and attach a handler to watch for changes.

        Raises OSError or a subclass of it if the MRU file cannot be stat()-ed.
        """
        path = self.manager.props.filename

        # Limit the most obvious side-channel for snooping on MRU info
        if not os.stat(path).st_mode & 0o777 == 0o600:
            log.warning("Bad file permissions on MRU list. Fixing: %s", path)
            try:
                os.chmod(path, 0o600)
            except OSError:
                log.error("Failed to chmod %s", path)

        # Actually scrub and attach
        self.scrub(self.manager)
        if self.handler is None:  # Don't double-connect our signals
            self.handler = self.manager.connect('changed', self.scrub)
            log.debug("Watching MRU list: %s", path)
        else:
            log.debug("Already watched. Skipping: %s", path)


def any_to_url(path_or_url: str) -> str:
    """Helper for flexible command-line input of blacklist entries"""
    is_abs = path_or_url.startswith(os.sep)
    is_alt_abs = os.altsep and path_or_url.startswith(os.altsep)
    exists = os.path.exists(path_or_url)

    # If it's something like /path/to/porn/folder-number- meant to match only
    # some children of a given folder
    exists_prefix = os.path.exists(os.path.split(path_or_url)[0])

    if is_abs or is_alt_abs or exists or exists_prefix:
        path_or_url = 'file://' + pathname2url(os.path.abspath(path_or_url))

    # Otherwise, trust the user to know what they meant
    return path_or_url


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
    resopt.add_argument('--config', action="store", metavar="FILE",
        default=os.path.join(XDG_DATA_DIR, 'grms.conf'),
        help="Specify a non-default config file (default: %(default)s)")

    nonres = parser.add_argument_group("Non-Resident Actions")
    nonres.add_argument('-a', '--add', action="append", dest="additions",
        help="Add URI to the list of blacklisted prefixes and exit.",
        default=[], metavar="URI")
    nonres.add_argument('-r', '--remove', action="append", dest="removals",
        help="Remove prefixes from the blacklist which match URI and exit.",
        default=[], metavar="URI")
    #nonres.add_argument('--once', action="store_true", default=False,
    #    help="Don't become resident. Just scrub and exit.")

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

    # TODO: Watch the config file for changes so the watcher doesn't need to be
    #       restarted manually to pick them up.
    if args.additions or args.removals:
        for arg in args.additions:
            blist.add(any_to_url(arg))
        for arg in args.removals:
            blist.remove_all(any_to_url(arg))
        blist.save()
        sys.exit(0)

    # Scrub and start watching the recent list for updates
    scrubber = RecentManagerScrubber(blist)
    scrubber.start()

    if args.purge:
        scrubber.purge()

    #if args.once:
    #    Gtk.main_iteration_do(True)
    #else:
    Gtk.main()

if __name__ == '__main__':  # pragma: nocover
    main()

# vim: set sw=4 sts=4 expandtab :
