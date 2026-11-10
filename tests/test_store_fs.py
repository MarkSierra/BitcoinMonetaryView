# SPDX-License-Identifier: AGPL-3.0-or-later
"""How the result database sits on disk: WAL checkpoint size and btrfs nodatacow."""

import os
import shutil
import struct
import tempfile
import unittest
from unittest import mock

from bitcoinmonetaryview import store

# CI mounts a small btrfs file system and points this at a directory on it
BTRFS_DIR = os.environ.get("BMV_BTRFS_DIR")


def flags(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        return struct.unpack("q", store.fcntl.ioctl(fd, store.FS_IOC_GETFLAGS, struct.pack("q", 0)))[0]
    finally:
        os.close(fd)


class WalCheckpoints(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_writer_checkpoints_every_64_mb(self):
        s = store.Store(os.path.join(self.dir, "regtest", "bmv.sqlite"))
        try:
            self.assertEqual(s.db.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertEqual(s.db.execute("PRAGMA wal_autocheckpoint").fetchone()[0], store.WAL_CHECKPOINT_PAGES)
            self.assertEqual(s.db.execute("PRAGMA journal_size_limit").fetchone()[0], store.WAL_SIZE_LIMIT)
            page = s.db.execute("PRAGMA page_size").fetchone()[0]
            self.assertEqual(store.WAL_CHECKPOINT_PAGES * page, 64 << 20)
        finally:
            s.close()


@unittest.skipUnless(store.fcntl and os.name == "posix", "Linux only")
class NoCow(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def fake_ioctl(self, current, fail=None):
        calls = []

        def ioctl(fd, req, arg):
            calls.append((req, struct.unpack("q", arg)[0]))
            if fail and req == fail:
                raise OSError(25, "Inappropriate ioctl for device")
            return struct.pack("q", current) if req == store.FS_IOC_GETFLAGS else arg
        return ioctl, calls

    def test_sets_the_attribute_and_keeps_other_flags(self):
        ioctl, calls = self.fake_ioctl(0x1000)
        with mock.patch.object(store.sys, "platform", "linux"), mock.patch.object(store.fcntl, "ioctl", ioctl):
            self.assertTrue(store.no_cow(self.dir))
        self.assertEqual(calls[-1], (store.FS_IOC_SETFLAGS, 0x1000 | store.FS_NOCOW_FL))

    def test_already_set_is_left_alone(self):
        ioctl, calls = self.fake_ioctl(store.FS_NOCOW_FL)
        with mock.patch.object(store.sys, "platform", "linux"), mock.patch.object(store.fcntl, "ioctl", ioctl):
            self.assertTrue(store.no_cow(self.dir))
        self.assertEqual([c[0] for c in calls], [store.FS_IOC_GETFLAGS])

    def test_other_file_systems_are_fine(self):
        for fail in (store.FS_IOC_GETFLAGS, store.FS_IOC_SETFLAGS):
            ioctl, _ = self.fake_ioctl(0, fail=fail)
            with mock.patch.object(store.sys, "platform", "linux"), mock.patch.object(store.fcntl, "ioctl", ioctl):
                self.assertFalse(store.no_cow(self.dir))
        self.assertFalse(store.no_cow(os.path.join(self.dir, "missing")))

    def test_store_marks_its_network_directory(self):
        seen = []
        with mock.patch.object(store, "no_cow", side_effect=seen.append):
            store.Store(os.path.join(self.dir, "mainnet", "bmv.sqlite")).close()
        self.assertEqual(seen, [os.path.join(self.dir, "mainnet")])


@unittest.skipUnless(BTRFS_DIR, "no btrfs test directory (set BMV_BTRFS_DIR)")
class NoCowOnBtrfs(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(dir=BTRFS_DIR)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_database_files_are_created_nodatacow(self):
        net = os.path.join(self.dir, "mainnet")
        s = store.Store(os.path.join(net, "bmv.sqlite"))
        try:
            s.begin()
            s.set_meta("probe", "1")
            s.commit()
            names = sorted(os.listdir(net))
            self.assertIn("bmv.sqlite", names)
            self.assertTrue(flags(net) & store.FS_NOCOW_FL, "directory not nodatacow")
            for name in names:
                self.assertTrue(flags(os.path.join(net, name)) & store.FS_NOCOW_FL, f"{name} not nodatacow")
        finally:
            s.close()

    def test_existing_files_keep_their_attribute(self):
        net = os.path.join(self.dir, "signet")
        os.makedirs(net)
        old = os.path.join(net, "spent-filter.bin")
        with open(old, "wb") as f:
            f.write(b"x" * 4096)
        store.Store(os.path.join(net, "bmv.sqlite")).close()
        self.assertFalse(flags(old) & store.FS_NOCOW_FL)
        self.assertTrue(flags(os.path.join(net, "bmv.sqlite")) & store.FS_NOCOW_FL)


if __name__ == "__main__":
    unittest.main()
