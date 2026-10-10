#!/usr/bin/env python3
"""Negative tests prove the filesystem audit detects loss/corruption."""
import copy
import pathlib
import stat
import tempfile
import unittest
import rebuild


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.original = {
            '.':dict(type=stat.S_IFDIR, mode=0o755, uid=0, gid=0, mtime=123, inode=1),
            'bin':dict(type=stat.S_IFDIR, mode=0o755, uid=0, gid=0, mtime=123, inode=2),
            'bin/player':dict(type=stat.S_IFREG, mode=0o755, uid=123, gid=456, mtime=123, inode=3, size=4, sha256='hash'),
            'bin/alias':dict(type=stat.S_IFREG, mode=0o755, uid=123, gid=456, mtime=123, inode=3, size=4, sha256='hash'),
            'bin/link':dict(type=stat.S_IFLNK, mode=0o777, uid=0, gid=0, mtime=123, inode=4, target='player'),
        }
    def audit(self, actual):
        return rebuild.validate(self.original, self.original, actual)
    def test_renumbered_inodes_valid(self):
        actual = copy.deepcopy(self.original)
        for row in actual.values():
            row['inode'] += 100
        self.assertTrue(self.audit(actual)['passed'])
    def test_changed_bytes_fail(self):
        actual = copy.deepcopy(self.original)
        actual['bin/player']['sha256'] = 'changed'
        self.assertFalse(self.audit(actual)['passed'])
    def test_changed_metadata_fail(self):
        for key, value in [('mode',0o644),('uid',0),('gid',0),('mtime',124)]:
            actual = copy.deepcopy(self.original)
            actual['bin/player'][key] = value
            self.assertFalse(self.audit(actual)['passed'], key)
    def test_changed_symlink_fail(self):
        actual = copy.deepcopy(self.original)
        actual['bin/link']['target'] = 'other'
        self.assertFalse(self.audit(actual)['passed'])
    def test_lost_hardlink_fail(self):
        actual = copy.deepcopy(self.original)
        actual['bin/alias']['inode'] = 99
        self.assertFalse(self.audit(actual)['passed'])
    def test_extra_hardlink_fail(self):
        actual = copy.deepcopy(self.original)
        actual['bin/link']['inode'] = 3
        self.assertFalse(self.audit(actual)['passed'])
    def test_deleted_path_fail(self):
        actual = copy.deepcopy(self.original)
        del actual['bin/link']
        self.assertEqual(self.audit(actual)['removed_original_paths'], ['bin/link'])
        self.assertFalse(self.audit(actual)['passed'])
    def test_root_change_fail(self):
        actual = copy.deepcopy(self.original)
        actual['.']['mode'] = 0o777
        self.assertFalse(self.audit(actual)['root_metadata_preserved'])
        self.assertFalse(self.audit(actual)['passed'])
    def test_overlay_metadata_and_hardlink_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root/'bin').mkdir()
            (root/'bin/player').write_bytes(b'new!')
            with self.assertRaisesRegex(ValueError, 'hardlink'):
                rebuild.overlays(root, self.original, 123)
            (root/'bin/alias').write_bytes(b'new!')
            (root/'lib').mkdir()
            (root/'lib/helper.so.1').write_bytes(b'library')
            (root/'lib/helper.so.1').chmod(0o755)
            (root/'bin/new').write_bytes(b'executable')
            (root/'bin/new').chmod(0o755)
            rows, _ = rebuild.overlays(root, self.original, 123)
            self.assertEqual(rows['bin/player']['uid'],123)
            self.assertEqual(rows['bin/player']['mode'],0o755)
            self.assertEqual(rows['lib/helper.so.1']['mode'],0o644)
            self.assertEqual(rows['bin/new']['mode'],0o755)
            self.assertEqual(rows['bin/new']['uid'],0)
    def test_reject_overlay_type_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root/'bin').write_bytes(b'not directory')
            with self.assertRaisesRegex(ValueError, 'inode type'):
                rebuild.overlays(root,self.original,123)

if __name__ == '__main__':
    unittest.main()
