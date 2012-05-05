GTK+ Recent Scrubber is a simple little tool for managing your global GTK+
recently-used files list.

So far, it will...

1. watch the default recent list for every screen of every display in your X11
   session and filter out URIs that match a blacklist of prefixes. (Like the
   [HistoryBlock](https://addons.mozilla.org/en-US/firefox/addon/historyblock/)
   extension for Firefox)
2. Make sure your `recently-used.xbel` files are only readable and writable by
   you.
3. Provide a simple `--purge` flag for non-GNOME users who just want to wipe
   everything.

In future, I also plan to implement a GUI so you can remove individual entries.

How is this useful? Well, everyone has _some_ guilty pleasure they don't want to draw attention to. Without this, having a recent files list isn't very useful because you keep having to clear it.

With it, you can simply blacklist your vices so they don't appear, while the rest of the list continues to function normally.

Even better, your blacklist is hashed, so it's easier for people to just snoop around the old fashioned way than to use it as a starting point.

It isn't _technically_ secure, because there is a few-second interval after programs put things into the list and before they're filtered out again, but it should be good enough for most people.

## Requirements

* Python 2.5+
* GTK 2.10+ (for `gtk.RecentManager`)
* PyGTK

##Installation

1. Put `gtk_cleanup.py` file wherever you want and name it whatever you want.
2. Chmod it executable.
3. Run `gtk_cleanup.py --add <URI or path>` to build your blacklist.
4. Run `gtk_cleanup.py -vv --once` to test it.
5. Use whatever means you normally would to make `gtk_cleanup.py` (no arguments) run on login.

See `gtk_cleanup.py --help` for other features.
