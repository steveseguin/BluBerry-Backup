"""Run with python -m unittest discover -s tests.

Load the packing functions directly from process.py so these stdlib-only tests do
not need image/metadata libraries. Only the worker pool/progress display are
replaced; file sizes and the production packing algorithm are real.
"""
import ast
from collections import Counter
from contextlib import redirect_stdout
import heapq
import io
import os
from pathlib import Path
import random
import tempfile
import unittest


class SerialPool:
    def __init__(self, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def imap(self, function, items):
        return map(function, items)


def load_packing():
    source_path = Path(__file__).resolve().parents[1] / 'process.py'
    module = ast.parse(source_path.read_text(encoding='utf-8'))
    names = {'get_album_structure', 'optimize_disc_packing'}
    module.body = [node for node in module.body
                   if isinstance(node, ast.FunctionDef) and node.name in names]
    namespace = {'os': os, 'heapq': heapq, 'Pool': SerialPool,
                 'getCPUs': lambda: 1, 'tqdm': lambda items, **kwargs: items}
    exec(compile(module, str(source_path), 'exec'), namespace)
    return namespace['optimize_disc_packing']


class DiscPackingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pack = staticmethod(load_packing())

    def run_case(self, groups, capacity=100, fill_ratio=0.9):
        with tempfile.TemporaryDirectory() as tmp:
            albums = []
            expected = []
            for index, sizes in enumerate(groups):
                # The scanner creates segments only for nonempty directories.
                if not sizes:
                    continue
                name = 'album_{}'.format(index)
                root = Path(tmp, name)
                root.mkdir()
                filenames = []
                for file_index, size in enumerate(sizes):
                    filename = 'photo_{}.jpg'.format(file_index)
                    Path(root, filename).write_bytes(b'x' * size)
                    filenames.append(filename)
                    if size <= capacity:
                        expected.append((name, name, filename, size))
                albums.append((name, name, sum(sizes), None, None,
                               len(sizes), str(root), filenames))
            with redirect_stdout(io.StringIO()):
                discs = self.pack(albums, capacity, fill_ratio)
            actual = [entry for disc in discs for entry in disc]
            self.assertEqual(Counter(expected), Counter(actual))
            for disc in discs:
                self.assertTrue(disc)
                self.assertLessEqual(sum(entry[3] for entry in disc), capacity)
            return discs

    def test_larger_file_is_preserved_after_smaller_file_fills_disc(self):
        discs = self.run_case([[60, 50, 40]])
        self.assertEqual([[60, 40], [50]],
                         [[entry[3] for entry in disc] for disc in discs])

    def test_larger_file_survives_repeated_smaller_fills(self):
        self.run_case([[60, 50, 20, 15, 5]])

    def test_multiple_larger_files_are_preserved(self):
        self.run_case([[60, 50, 50, 40, 40]])

    def test_cross_album_fill_preserves_each_file(self):
        self.run_case([[60, 50, 40], [80, 10, 10], [55, 45]])

    def test_exact_capacity_and_rollover(self):
        self.run_case([[100, 60, 40, 90, 10, 1]])

    def test_no_smaller_file_fits(self):
        self.run_case([[60, 50, 45]])

    def test_empty_albums_and_zero_byte_files(self):
        self.run_case([[], [0, 0, 60, 50, 40]])
        self.assertEqual([], self.run_case([]))

    def test_oversize_files_keep_existing_explicit_skip_policy(self):
        self.run_case([[101, 60, 50, 40, 200]])

    def test_custom_fill_ratio(self):
        for ratio in (0, 0.5, 0.9, 1):
            with self.subTest(ratio=ratio):
                self.run_case([[60, 50, 20, 15, 5]], fill_ratio=ratio)

    def test_seeded_randomized_preservation_and_capacity(self):
        randomizer = random.Random(271828)
        for case in range(100):
            groups = [[randomizer.randrange(0, 125)
                       for _ in range(randomizer.randrange(0, 12))]
                      for _ in range(randomizer.randrange(1, 5))]
            with self.subTest(case=case, groups=groups):
                self.run_case(groups)


if __name__ == '__main__':
    unittest.main()
