#!/usr/bin/env python

import unittest

from pydra.cluster.tasks.datasource.selector import DirSelector, FileSelector

class DirSelectorCheeseTest(unittest.TestCase):

    def setUp(self):

        self.ds = DirSelector("cheeses")

    def test_length(self):

        self.assertEqual(len(self.ds), 2)

    def test_key(self):

        self.assertTrue(hasattr(self.ds, "key") and self.ds.key)

class FileSelectorTest(unittest.TestCase):

    def setUp(self):

        self.fs = FileSelector("cheeses/cheddar.txt")

    def test_handle(self):

        handle = self.fs.handle
        self.assertTrue(len(handle))

        handle2 = self.fs.handle
        self.assertEqual(handle, handle2)

    def test_key(self):

        self.assertTrue(hasattr(self.fs, "key") and self.fs.key)

if __name__ == "__main__":
    import os.path
    os.chdir(os.path.dirname(__file__))
    unittest.main()
