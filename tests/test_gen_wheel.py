"""gen/python/build_wheel.py: the wheel of the generated code is the same bytes from the same files, holds only
the generated package's .py files, and its RECORD is right."""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import os
import tempfile
import unittest
import zipfile
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("build_wheel", os.path.join(ROOT, "gen", "python", "build_wheel.py"))
bw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bw)

FILES = {
    "machinery/__init__.py": b"",
    "machinery/plugin/v1alpha1/plugin_pb.py": b"# messages\n",
    "machinery/plugin/v1alpha1/plugin_connect.py": b"# service\n",
    "machinery/plugin/v1alpha1/notes.txt": b"not code\n",
    "machinery/plugin/v1alpha1/__pycache__/plugin_pb.cpython-312.pyc": b"\x00",
    "__init__.py": b"# outside the package\n",
}


def tree(tmp, files=FILES):
    src = os.path.join(tmp, "src")
    for name, data in files.items():
        path = os.path.join(src, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
    return src


class Wheel(unittest.TestCase):
    def build(self, tmp, out="out"):
        os.makedirs(os.path.join(tmp, out))
        return bw.build(os.path.join(tmp, out), "0.1.0", bw.sources(tree(tmp)))

    def test_same_files_give_the_same_bytes(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            with open(self.build(a), "rb") as f, open(self.build(b), "rb") as g:
                self.assertEqual(f.read(), g.read())

    def test_only_the_packages_py_files_and_the_dist_info(self):
        with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(self.build(tmp)) as z:
            self.assertEqual(z.namelist(), [
                "machinery/__init__.py", "machinery/plugin/v1alpha1/plugin_connect.py",
                "machinery/plugin/v1alpha1/plugin_pb.py", "machinery_gen-0.1.0.dist-info/METADATA",
                "machinery_gen-0.1.0.dist-info/WHEEL", "machinery_gen-0.1.0.dist-info/RECORD"])
            self.assertEqual(os.path.basename(z.filename), "machinery_gen-0.1.0-py3-none-any.whl")

    def test_every_entry_is_dated_1980_mode_0644_and_stored(self):
        with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(self.build(tmp)) as z:
            for info in z.infolist():
                self.assertEqual(info.date_time, (1980, 1, 1, 0, 0, 0), info.filename)
                self.assertEqual(info.external_attr >> 16, 0o100644, info.filename)
                self.assertEqual(info.compress_type, zipfile.ZIP_STORED, info.filename)

    def test_record_hashes_and_sizes_every_other_entry(self):
        with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(self.build(tmp)) as z:
            rows = [line.split(",") for line in z.read("machinery_gen-0.1.0.dist-info/RECORD").decode().splitlines()]
            self.assertEqual([r[0] for r in rows], z.namelist())
            self.assertEqual(rows[-1], ["machinery_gen-0.1.0.dist-info/RECORD", "", ""])
            for name, digest, size in rows[:-1]:
                data = z.read(name)
                want = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
                self.assertEqual((digest, size), (f"sha256={want}", str(len(data))), name)

    def test_metadata_names_the_version_and_pins_the_runtime(self):
        with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(self.build(tmp)) as z:
            meta = z.read("machinery_gen-0.1.0.dist-info/METADATA").decode().splitlines()
            for line in ("Name: machinery-gen", "Version: 0.1.0", "Requires-Python: >=3.11",
                         "Requires-Dist: protobuf-py==0.7.0", "Requires-Dist: connectrpc==0.13.0"):
                self.assertIn(line, meta)
            self.assertIn("Tag: py3-none-any", z.read("machinery_gen-0.1.0.dist-info/WHEEL").decode().splitlines())


class Sources(unittest.TestCase):
    def test_sorted_whatever_order_the_directory_lists(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = tree(tmp)
            walked = list(os.walk(os.path.join(src, "machinery")))
            backwards = [(root, dirs, sorted(files, reverse=True)) for root, dirs, files in reversed(walked)]
            with mock.patch.object(bw.os, "walk", return_value=backwards):
                names = [p for p, _ in bw.sources(src)]
            self.assertEqual(names, sorted(names))

    def test_a_tree_without_generated_messages_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = tree(tmp, {"machinery/__init__.py": b""})
            with self.assertRaises(SystemExit):
                bw.sources(src)


class Version(unittest.TestCase):
    def read(self, text):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "VERSION")
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(text)
            return bw.version(path)

    def test_one_line_x_y_z(self):
        self.assertEqual(self.read("0.1.0\n"), "0.1.0")
        self.assertEqual(self.read("10.20.30\n"), "10.20.30")

    def test_anything_else_is_refused(self):
        for text in ("0.1.0", "0.1.0\n\n", "v0.1.0\n", "0.1\n", "01.2.3\n", "0.1.0-rc1\n", " 0.1.0\n", "0.1.0\r\n",
                     "0.1.0\n0.2.0\n", "1٠.1.0\n"):
            with self.subTest(text=text), self.assertRaises(SystemExit):
                self.read(text)

    def test_the_committed_version_reads(self):
        self.assertRegex(bw.version(), r"^\d+\.\d+\.\d+$")


if __name__ == "__main__":
    unittest.main()
