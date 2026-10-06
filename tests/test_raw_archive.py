"""Stdlib-only scanner and synthetic archival-byte regressions.

Run with python -m unittest discover -s tests. Load production functions without
optional image/metadata dependencies. Metadata, the worker pool and progress
display are substituted; selection, packing, copy/move and manifests are real.
These tests do not decode RAW files or exercise the HTML gallery.
"""
import ast
from collections import defaultdict
from contextlib import redirect_stdout
from datetime import datetime
import hashlib
import heapq
import io
import json
import logging
import os
from pathlib import Path
import shutil
import tempfile
import threading
import traceback
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


def load_archive_functions():
    source_path = Path(__file__).resolve().parents[1] / 'process.py'
    module = ast.parse(source_path.read_text(encoding='utf-8'))
    functions = {'get_segmented_albums', 'get_album_info', 'get_album_structure',
                 'optimize_disc_packing', 'init_worker', 'process_file',
                 'get_file_hash', 'create_manifest_file'}
    constants = {'image_extensions', 'video_extensions', 'raw_video_extensions',
                 'raw_image_extensions', 'all_extensions'}
    module.body = [node for node in module.body if
                   (isinstance(node, ast.FunctionDef) and node.name in functions)
                   or (isinstance(node, ast.Assign) and any(
                       isinstance(target, ast.Name) and target.id in constants
                       for target in node.targets))]
    namespace = {
        'os': os, 'shutil': shutil, 'datetime': datetime, 'heapq': heapq,
        'json': json, 'hashlib': hashlib, 'defaultdict': defaultdict,
        'logging': logging, 'traceback': traceback, 'Pool': SerialPool,
        'getCPUs': lambda: 1, 'tqdm': lambda items, **kwargs: items,
        'get_date_taken': lambda path: datetime(2024, 1, 1),
    }
    exec(compile(module, str(source_path), 'exec'), namespace)
    return namespace


class RawArchiveTests(unittest.TestCase):
    def setUp(self):
        self.archive = load_archive_functions()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.source = Path(self.temporary.name, 'source')
        self.source.mkdir()

    def write_files(self, names):
        contents = {}
        for name in names:
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            contents[name] = ('Synthetic archival bytes: ' + name).encode('utf-8')
            path.write_bytes(contents[name])
        return contents

    def scan(self, files_per_segment=300):
        with redirect_stdout(io.StringIO()):
            return self.archive['get_segmented_albums'](
                str(self.source), files_per_segment=files_per_segment)

    def selected_paths(self, albums):
        return [(Path(root) / filename).relative_to(self.source).as_posix()
                for root, _, _, filenames in albums for filename in filenames]

    def raw_names(self, upper=False):
        return ['photo' + (ext.upper() if upper else ext)
                for ext in sorted(self.archive['raw_image_extensions'])]

    def test_each_declared_raw_photo_format_is_selected(self):
        names = self.raw_names()
        self.write_files(names)
        self.assertCountEqual(names, self.selected_paths(self.scan()))

    def test_uppercase_raw_photo_extensions_are_selected(self):
        names = self.raw_names(upper=True)
        self.write_files(names)
        self.assertCountEqual(names, self.selected_paths(self.scan()))

    def test_raw_only_album_is_not_discarded(self):
        self.write_files(['holiday/DSC_0001.NEF', 'holiday/DSC_0002.NEF'])
        albums = self.scan()
        self.assertEqual(1, len(albums))
        self.assertCountEqual(['holiday/DSC_0001.NEF', 'holiday/DSC_0002.NEF'],
                              self.selected_paths(albums))

    def test_segmented_nested_raw_albums_preserve_each_file_once(self):
        names = ['album/nested/photo_{:03d}.DNG'.format(i) for i in range(307)]
        self.write_files(names)
        albums = self.scan()
        self.assertEqual([7, 300], sorted(len(album[3]) for album in albums))
        self.assertCountEqual(names, self.selected_paths(albums))

    def test_media_and_json_controls_remain_selected(self):
        extensions = self.archive['image_extensions'] | self.archive['video_extensions']
        names = ['control' + ext for ext in sorted(extensions)]
        names += ['control.raw', 'control.DNG.json', 'album.json']
        self.write_files(names)
        self.assertCountEqual(names, self.selected_paths(self.scan()))

    def test_unrecognized_files_remain_excluded(self):
        self.write_files(['notes.txt', 'document.pdf', 'index.html', 'app.py',
                          'unknown.rawx', 'photo.dng.bak'])
        self.assertEqual([], self.scan())

    def archive_case(self, move):
        names = ['holiday/' + name for name in self.raw_names(upper=True)]
        names += ['holiday/photo.JPG', 'holiday/photo.DNG.json']
        original_contents = self.write_files(names)
        destination = Path(self.temporary.name, 'destination')
        disc_dir = destination / 'Disc_1'
        disc_dir.mkdir(parents=True)
        with redirect_stdout(io.StringIO()):
            albums = [self.archive['get_album_info'](album) for album in self.scan()]
            # All files fit so this cannot exercise the separate gap-fill defect.
            discs = self.archive['optimize_disc_packing'](albums, 1024 * 1024)
        self.assertEqual(1, len(discs))
        self.archive['init_worker'](str(self.source), str(destination), move,
                                    {}, threading.Lock())
        results = [self.archive['process_file']((entry, str(disc_dir),
                   str(destination / 'processed_files.log'))) for entry in discs[0]]
        self.assertEqual([], [result[3] for result in results if result[3]])
        self.assertEqual(len(names), len(results))
        for name, contents in original_contents.items():
            self.assertEqual(contents, (disc_dir / name).read_bytes())
            self.assertEqual(not move, (self.source / name).exists())
            if not move:
                self.assertEqual(contents, (self.source / name).read_bytes())
        album_dir = disc_dir / 'holiday'
        self.archive['create_manifest_file'](str(album_dir))
        manifest = json.loads((album_dir / 'hash_manifest.json').read_text())
        expected = {hashlib.sha256(contents).hexdigest(): [Path(name).name]
                    for name, contents in original_contents.items()}
        self.assertEqual(expected, manifest)

    def test_raw_originals_and_sidecar_are_copied_and_manifested(self):
        self.archive_case(move=False)

    def test_raw_originals_and_sidecar_are_moved_and_manifested(self):
        self.archive_case(move=True)


if __name__ == '__main__':
    unittest.main()
