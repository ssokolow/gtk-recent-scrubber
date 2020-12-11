#!/usr/bin/env python3

import os, shutil, tempfile, unittest, uuid

from gtk_cleanup import Blacklist, any_to_url


class TestBlacklist(unittest.TestCase):
    """Tests for the 'Blacklist' class"""
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.blacklist_path = os.path.join(self.temp_dir, 'temp.conf')
        self.bl = Blacklist(self.blacklist_path)

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_hash_prefix(self):
        """Blacklist._hash_prefix: Basic function"""
        self.assertEqual(type(Blacklist._hash_prefix('test string')),
                         type('string'),
                         "_hash_method must return the standard string type")
        self.assertNotEqual(Blacklist._hash_prefix('Test String A'),
                            Blacklist._hash_prefix('Test String B'),
                            'Different strings must have different hashes')
        # TODO: Test how hashlib.sha1 behaves when given a non-ASCII path in
        # the Python 2.x version and then match that behaviour here.

    def test_hash_prefix_compat(self):
        """Blacklist._hash_prefix: Compatibility with existing blacklists"""
        self.assertEqual(Blacklist._hash_prefix('file:///bin/sh'),
            '765e4c4a771b301deb2c65918ce0e599cf7049e8',
            "_hash_method cannot change its algorithm without breaking "
            "compatibility with existing blacklists")

    def test_add_deduping(self):
        """Blacklist.add: Deduplicates additions"""
        self.assertEqual(self.bl._contents, [])
        self.bl.add('foo')
        self.assertEqual(self.bl._contents, [(3, self.bl._hash_prefix('foo'))])
        self.bl.add('foo')
        self.assertEqual(self.bl._contents, [(3, self.bl._hash_prefix('foo'))])

        self.bl.add('fooo')
        self.bl.add('foooo')
        self.bl.add('fooooo')
        self.bl.add('foooooo')
        self.assertEqual(self.bl._contents, [(3, self.bl._hash_prefix('foo'))])

    def test_index_no_match(self):
        """Blacklist.index: Raises IndexError on no match"""
        self.assertRaises(IndexError, self.bl.index, 'nonexistent')

    def test_index(self):
        """Blacklist.index: Prefix matching behaviour is correct

        (i.e. it doesn't special-case path separators because that might change
        how blacklists built under v0.1 are interpreted.)
        """
        self.assertRaises(IndexError, self.bl.index, 'file:///bin/sh')
        self.bl.add('file:///bin/sh')
        self.assertEqual(self.bl.index('file:///bin/sh'), 0)
        self.assertEqual(self.bl.index('file:///bin/shar'), 0)
        self.assertEqual(self.bl.index('file:///bin/sh/'), 0)
        self.assertEqual(self.bl.index('file:///bin/sh/foo'), 0)
        self.assertRaises(IndexError, self.bl.index, 'file:///bin/bar')

        self.assertRaises(IndexError, self.bl.index, 'file:///bin/bash/')
        self.bl.add('file:///bin/bash/')
        self.assertRaises(IndexError, self.bl.index, 'file:///bin/bash')
        self.assertRaises(IndexError, self.bl.index, 'file:///bin/bashar')
        self.assertEqual(self.bl.index('file:///bin/bash/'), 1)
        self.assertEqual(self.bl.index('file:///bin/bash/foo'), 1)

    def test_load_nonexistent(self):
        """Blacklist: Handles nonexistent blacklists gracefully"""
        self.assertFalse(Blacklist(str(uuid.uuid4())).load())

    def test_load_failures(self):
        """Blacklist: Handles miscellaneous load failures gracefully"""
        # Access Denied
        self.assertRaises(OSError,
            lambda: Blacklist("/etc/ssl/private").load())

        # Invalid UTF-8
        self.assertFalse(Blacklist("/bin/sh").load())

    def test_load_malformed(self):
        """Blacklist: Handles malformed blacklists gracefully"""
        with open(self.blacklist_path, 'w') as fobj:
            fobj.write('765e4c4a771b301deb2c65918ce0e599cf7049e8 14\n')
            fobj.flush()

            # Sanity check that load succeeds with well-formed data
            self.assertTrue(self.bl.load())

        for (data, message) in (
                    ('a 14\n', 'field 0 length is wrong'),
                    ('765e4c4a771b301deb2c65918ce0e599cf7049e8 a\n',
                        'field 1 not an int'),
                    ('765e4c4a771b301deb2c65918ce0e599cf7049e8\n',
                        'there are less than 2 fields'),
                    ('765e4c4a771b301deb2c65918ce0e599cf7049e8 14 1\n',
                        'there are more than 2 fields'),
        ):
            with open(self.blacklist_path, 'w') as fobj:
                bl = Blacklist(self.blacklist_path)
                fobj.write(data)
                fobj.flush()
                self.assertFalse(bl.load(),
                    'Should fail if {}'.format(message))

    def test_load_deduplicates(self):
        """Blacklist.load: Duplicate lines are collapsed away"""
        with open(self.blacklist_path, 'w') as fobj:
            # check that it deduplicates properly, even if data gets unsorted
            # by external means
            fobj.write('765e4c4a771b301deb2c65918ce0e599cf7049e8 14\n')
            fobj.write('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa 1\n')
            fobj.write('765e4c4a771b301deb2c65918ce0e599cf7049e8 14\n')
            fobj.flush()

        self.assertTrue(self.bl.load())
        self.assertEqual(len(self.bl._contents), 2)

    def test_load_success(self):
        self.fail("TODO: Test that load produces the expected result from "
                  "known input")

    def test_load_comments(self):
        """Blacklist.load: Allows and ignores comments and whitespace"""
        self.fail("TODO: Test how load handles comments and whitespace, "
                  "including variable whitespace between fields")
        self.fail("TODO: Test that a load() / save() cycle strips comments "
                  "since the sorting process would ruin their context.")

    def test_remove_all(self):
        """Blacklist.remove_all: Basic function"""
        # Set up and assert step 1 test data
        self.assertEqual(self.bl._contents, [])
        self.bl.add('foo')
        self.bl.add('foba')
        self.assertEqual(self.bl.index('foo'), 0)
        self.assertEqual(self.bl.index('foba'), 1)

        # Assert that removing 'foo' doesn't remove 'fob' or leave a hole
        self.bl.remove_all('foo')
        self.assertRaises(IndexError, self.bl.index, 'foo')
        self.assertEqual(self.bl.index('fobaa'), 0)

        # Assert that the test data remains sorted
        # (Sorted by the prefix lengths and then hashes, not the raw text)
        self.bl.add('foo')
        self.bl.add('fa')
        self.assertEqual(self.bl.index('fa'), 0)
        self.assertEqual(self.bl.index('fobaaa'), 2)
        self.assertEqual(self.bl.index('foo'), 1)

        # Assert that removing 'fo' doesn't remove 'foo' or 'fob'
        self.bl.remove_all('fo')
        self.assertEqual(len(self.bl._contents), 3,
            "Removing a string should not remove strings with it as a prefix")

        # But the reverse relationship does work
        self.bl.remove_all('foooooooooo')
        self.bl.remove_all('fobaaaaaaaa')
        self.assertEqual(self.bl.index('faa'), 0)
        self.assertEqual(len(self.bl._contents), 1,
            "Removing a string should remove all its prefixes")

        # Assert that removing 'fa' returns to an empty list
        self.bl.remove_all('fa')
        self.assertEqual(self.bl._contents, [])

    def test_remove_all_is_all(self):
        """Blacklist: remove_all doesn't stop at the first match"""
        self.fail("TODO: Manually build a test file with overlapping prefixes "
            "and test that remove_all removes all of them.")

    def test_save_success(self):
        self.fail("TODO: Test that save produces the expected result from "
                  "known input")


class TestMisc(unittest.TestCase):
    """Tests that don't fit in any other category"""
    def test_any_to_url(self):  # nosec
        """any_to_url: Basic Functionality"""
        old_dir = os.getcwd()

        try:
            os.chdir('/bin')
            fname = str(uuid.uuid4())  # A reliably nonexistent filename
            self.assertEqual(any_to_url('/' + fname), 'file:///' + fname,
                "Absolute paths should always be translated to URLs")
            self.assertEqual(any_to_url('sh'), 'file:///bin/sh',
                "Relative paths which exist should be translated to URLs")
            self.assertEqual(any_to_url('sh/f'), 'file:///bin/sh/f',
                "A prefix as a trailing path component shouldn't break things")
            self.assertEqual(any_to_url('file:///f'), 'file:///f',
                "URLs should pass through unchanged")
        finally:
            os.chdir(old_dir)  # pragma: nocover

if __name__ == '__main__':  # pragma: nocover
    unittest.main()
