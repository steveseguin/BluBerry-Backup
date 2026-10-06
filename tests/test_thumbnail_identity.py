"""Offline actual-source gallery checks using real Pillow and a serial pool adapter."""
import ast
import contextlib
import hashlib
from html.parser import HTMLParser
import io
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
import urllib.parse
from PIL import Image

SOURCE = Path(os.environ.get('GALLERY_SOURCE', Path(__file__).parents[1] / 'process.py'))

class Progress:
    def __init__(self, iterable=None, **kwargs): self.iterable = iterable
    def __iter__(self): return iter(self.iterable)
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def update(self, *args): pass

class SerialPool:
    def __init__(self, **kwargs): pass
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def imap_unordered(self, fn, items): return map(fn, items)

class GalleryParser(HTMLParser):
    def __init__(self): super().__init__(); self.links = []; self.thumbs = []
    def handle_starttag(self, tag, attributes):
        a = dict(attributes)
        if tag == 'a' and a.get('class') == 'thumbnail-link': self.links.append(a['href'])
        if tag == 'img' and a.get('class') == 'thumbnail': self.thumbs.append(a['data-src'])

def load_gallery(path):
    tree = ast.parse(path.read_text())
    names = {'create_thumbnail', 'create_thumbnail_wrapper', 'generate_html_gallery', 'generate_html_structure', 'getCPUs'}
    assignments = {'image_extensions', 'video_extensions', 'raw_image_extensions', 'raw_video_extensions'}
    nodes = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name in names) or (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in assignments for t in n.targets))]
    def forbidden(*args, **kwargs): raise AssertionError('External codec is outside this test scope')
    ns = {'os': os, 'Image': Image, 'urllib': urllib, 'hashlib': hashlib,
          'tqdm': Progress, 'multiprocessing': types.SimpleNamespace(Pool=SerialPool, cpu_count=lambda: 2),
          'subprocess': types.SimpleNamespace(run=forbidden, CalledProcessError=subprocess.CalledProcessError, DEVNULL=-3, PIPE=-1),
          'rawpy': types.SimpleNamespace(imread=forbidden)}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), ns)
    return ns

class ThumbnailIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='bluberry-gallery-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ns = load_gallery(SOURCE)
    def image(self, name, color, fmt):
        p = self.root / name; p.parent.mkdir(parents=True, exist_ok=True)
        Image.new('RGB', (240, 180), color).save(p, fmt)
        return p
    def run_gallery(self):
        with contextlib.redirect_stdout(io.StringIO()): self.ns['generate_html_gallery'](str(self.root))
        parser = GalleryParser(); parser.feed((self.root / 'index.html').read_text())
        return {urllib.parse.unquote(a): urllib.parse.unquote(b) for a, b in zip(parser.links, parser.thumbs)}
    def assert_colors(self, mapping, expected):
        self.assertEqual(set(mapping), set(expected))
        self.assertEqual(len(set(mapping.values())), len(expected), 'distinct originals must have distinct thumbnail paths')
        for name, color in expected.items():
            with self.subTest(name=name), Image.open(self.root / mapping[name]) as thumb:
                actual = thumb.convert('RGB').getpixel((0, 0))
                self.assertTrue(all(abs(a-b) <= 4 for a,b in zip(actual,color)), (name,actual,color))
                self.assertLessEqual(max(thumb.size), 200)
    def test_same_stem_jpeg_png(self):
        self.image('Album/shot.jpg', (250,0,0), 'JPEG'); self.image('Album/shot.png',(0,0,250),'PNG')
        self.assert_colors(self.run_gallery(), {'Album/shot.jpg':(250,0,0),'Album/shot.png':(0,0,250)})
    def test_same_stem_three_formats(self):
        names = [('jpeg',(250,0,0),'JPEG'),('png',(0,250,0),'PNG'),('gif',(0,0,250),'GIF')]
        for ext,color,fmt in names: self.image('Album/photo.'+ext,color,fmt)
        self.assert_colors(self.run_gallery(), {'Album/photo.'+ext:color for ext,color,_ in names})
    def test_mixed_case_extensions(self):
        self.image('Album/scan.JPG',(250,0,0),'JPEG');self.image('Album/scan.PNG',(0,0,250),'PNG')
        self.assert_colors(self.run_gallery(), {'Album/scan.JPG':(250,0,0),'Album/scan.PNG':(0,0,250)})
    def test_unicode_spaces_and_url_characters(self):
        self.image('Summer 2024/旅 #1.jpg',(250,0,0),'JPEG');self.image('Summer 2024/旅 #1.png',(0,0,250),'PNG')
        self.assert_colors(self.run_gallery(), {'Summer 2024/旅 #1.jpg':(250,0,0),'Summer 2024/旅 #1.png':(0,0,250)})
    def test_nested_directories_independent(self):
        self.image('Album/A/photo.jpg',(250,0,0),'JPEG');self.image('Album/B/photo.jpg',(0,0,250),'JPEG')
        self.assert_colors(self.run_gallery(), {'Album/A/photo.jpg':(250,0,0),'Album/B/photo.jpg':(0,0,250)})
    def test_ordinary_single_thumbnail(self):
        self.image('Album/plain.jpg',(100,100,100),'JPEG')
        self.assert_colors(self.run_gallery(), {'Album/plain.jpg':(100,100,100)})
    def test_long_filename(self):
        name='Album/'+('x'*248)+'.jpg';self.image(name,(250,0,0),'JPEG')
        self.assert_colors(self.run_gallery(),{name:(250,0,0)})
    def test_rerun_stable_original_bytes(self):
        originals=[self.image('Album/take.jpg',(250,0,0),'JPEG'),self.image('Album/take.png',(0,0,250),'PNG')]
        before={p:p.read_bytes() for p in originals}
        first=self.run_gallery();second=self.run_gallery();self.assertEqual(first,second)
        self.assertEqual(before,{p:p.read_bytes() for p in originals})
        self.assert_colors(second,{'Album/take.jpg':(250,0,0),'Album/take.png':(0,0,250)})

if __name__ == '__main__': unittest.main(verbosity=2)
