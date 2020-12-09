[![Code Quality](https://landscape.io/github/ssokolow/gtk-recent-scrubber/master/landscape.png)](https://landscape.io/github/ssokolow/gtk-recent-scrubber/master)

GTK Recent Scrubber is a simple little tool for managing your global GTK
recently-used files list.

So far, it will...

1. Watch the GTK recent files list for your desktop session and filter out URIs
   that match a blacklist of prefixes. (Like the
   [HistoryBlock](https://addons.mozilla.org/en-US/firefox/addon/historyblock/)
   extension for Firefox)
2. Make sure your `recently-used.xbel` files are only readable and writable by
   you.
3. Provide a simple `--purge` flag for non-GNOME users who just want to wipe
   everything.

In the future, I also plan to implement a GUI so you can remove individual
entries as well as add support for filtering other lists.

How is this useful? Well, everyone has _some_ guilty pleasure they don't want to
draw attention to. Without this, having a recent files list isn't very useful
because you keep having to clear it.

With it, you can simply blacklist your vices so they don't appear, while the
rest of the list continues to function normally.

Even better, your blacklist is hashed, so it's easier for people to just snoop
around the old fashioned way than to use it as a starting point.

It isn't _technically_ secure, because there is a few-second interval after
programs put things into the list and before they're filtered out again, but it
should be good enough for most people.

## Requirements

- Python 3.x
- GTK 3.x (for `Gtk.RecentManager`)
- PyGObject with the GIR definitions for Gtk, Gdk, and GLib

**Debian and derivatives (Ubuntu, Mint, etc.):**

```sh
sudo apt-get install python3 python3-gi gir1.2-glib-2.0 gir1.2-gtk-3.0
```

**Fedora and derivatives:**

```sh
sudo dnf install python3 python3-gobject gtk3
```

## Installation

1. Put `gtk_cleanup.py` file wherever you want and name it whatever you want.
2. Mark it executable. (i.e. `chmod +x gtk_cleanup.py`)
3. Run `gtk_cleanup.py --add <URI or path>` to build your blacklist.
4. Run `gtk_cleanup.py -vv --once` to test it.
5. Use whatever means you normally would to make `gtk_cleanup.py` (no arguments)
   run on login.

See `gtk_cleanup.py --help` for other features.
