import os
import shutil
from datetime import datetime
from PIL import Image
import json
import sys
from tqdm import tqdm
from exif import Image as ExifImage
import exiftool
import multiprocessing
from multiprocessing import Manager, Value, Lock, Queue, Pool
from functools import partial
import subprocess
from collections import defaultdict
import hashlib
from contextlib import contextmanager
import time
import heapq
from collections import deque
from concurrent.futures import ProcessPoolExecutor
import heapq
import itertools
from concurrent.futures import as_completed
import traceback
import logging
import urllib.parse
import rawpy
import warnings
warnings.filterwarnings("ignore", category=UserWarning, module="PIL.Image")

# Global variables for shared resources
et = None
source_dir_global = None
dest_dir_global = None
move_files_global = False
file_hashes = None
log_lock = None

def init_worker(shared_source_dir, shared_dest_dir, shared_move_files, shared_file_hashes, shared_log_lock):
    global source_dir_global, dest_dir_global, move_files_global, file_hashes, log_lock
    source_dir_global = shared_source_dir
    dest_dir_global = shared_dest_dir
    move_files_global = shared_move_files
    file_hashes = shared_file_hashes
    log_lock = shared_log_lock
    
def get_segmented_albums(source_dir, files_per_segment=300):
    print("get_segmented_albums")
    segmented_albums = []
    for root, _, _ in os.walk(source_dir):
        if 'thumbs' in root or 'exiftool_files' in root or 'ignore' in root:
            continue
        
        album_name = os.path.relpath(root, source_dir)
        album_files = [f for f in os.listdir(root) if os.path.splitext(f)[1].lower() in all_extensions or f.endswith('.json')]
        
        # Segment the album if it has more than files_per_segment files
        maxSeg = 0
        for i in range(0, len(album_files), files_per_segment):
            segment = album_files[i:i+files_per_segment]
            segment_name = f"{album_name}_{i//files_per_segment + 1}" if i > 0 else album_name
            segmented_albums.append((root, album_name, segment_name, segment))
            maxSeg = i
        if maxSeg:
            print("Segmented folder "+str(maxSeg)+" times")
            
    
    return segmented_albums
    
def get_album_structure(album):
    album_name, segment_name, total_size, _, _, _, album_root, file_list = album
    structure = {}
    for filename in file_list:
        file_path = os.path.join(album_root, filename)
        file_size = os.path.getsize(file_path)
        relative_path = filename  # Since file_list contains relative paths
        year_month = os.path.dirname(relative_path)
        if year_month not in structure:
            structure[year_month] = []
        structure[year_month].append((relative_path, file_size))
    return album_name, segment_name, structure, total_size

@contextmanager
def exiftool_context():
    global et
    et = exiftool.ExifToolHelper()
    try:
        yield et
    finally:
        et.terminate()
        et = None
        time.sleep(0.1)  # Give a small delay for the process to terminated
        
def get_album_files(album):
    album_name, _, _, _, _, album_root = album
    files = []
    for root, _, filenames in os.walk(album_root):
        for filename in filenames:
            file_path = os.path.join(root, filename)
            file_size = os.path.getsize(file_path)
            relative_path = os.path.relpath(file_path, album_root)
            files.append((relative_path, file_size))
    return files
    
# Extend the list of supported formats
image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp', '.heic'}
video_extensions = {'.mp4', '.avi', '.mov', '.wmv', '.flv', '.mkv', '.webm', '.m4v', '.mpg', '.mpeg', '.mts'}
raw_video_extensions = {'.raw'}  # Add any specific RAW video formats here
raw_image_extensions = {'.orf', '.raw', '.cr2', '.nef', '.arw', '.dng', '.raf', '.rw2', '.pef', '.srw'}

skip_files = {'hash_manifest.json', 'index.html'}
all_extensions = image_extensions.union(video_extensions).union(raw_video_extensions)

def create_manifest_file(directory):
    manifest = defaultdict(list)
    for root, _, files in os.walk(directory):
        for file in files:
            if file == 'hash_manifest.json':
                continue
            file_path = os.path.join(root, file)
            relative_path = os.path.relpath(file_path, directory)
            file_hash = get_file_hash(file_path)
            manifest[file_hash].append(relative_path)
    
    manifest_path = os.path.join(directory, 'hash_manifest.json')
    with open(manifest_path, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=2)
 


def get_file_hash(file_path, block_size=65536):
    hasher = hashlib.sha256()
    with open(file_path, 'rb') as file:
        buffer = file.read(block_size)
        while len(buffer) > 0:
            hasher.update(buffer)
            buffer = file.read(block_size)
    return hasher.hexdigest()
    
def get_date_taken(file_path):
    global et
    file_ext = os.path.splitext(file_path)[1].lower()
    
    # Handle JSON files
    if file_ext == '.json':
        try:
            with open(file_path, 'r', encoding='utf-8') as json_file:
                json_data = json.load(json_file)
                
                # First, try to get photoTakenTime
                if 'photoTakenTime' in json_data and 'timestamp' in json_data['photoTakenTime']:
                    try:
                        return datetime.fromtimestamp(int(json_data['photoTakenTime']['timestamp']))
                    except (ValueError, OSError, OverflowError) as e:
                        print(f" Error parsing photoTakenTime for {file_path}: {e}")
                
                # If photoTakenTime is not available or invalid, try creationTime
                if 'creationTime' in json_data and 'timestamp' in json_data['creationTime']:
                    try:
                        return datetime.fromtimestamp(int(json_data['creationTime']['timestamp']))
                    except (ValueError, OSError, OverflowError) as e:
                        print(f" Error parsing creationTime for {file_path}: {e}")
                
                print(f" No valid date found in JSON for {file_path}")
        except json.JSONDecodeError as e:
            print(f" Error decoding JSON for {file_path}: {e}")
        except Exception as e:
            print(f" Error reading JSON data for {file_path}: {e}")
    
    # Use exiftool for all image and video types
    with exiftool_context() as et:
        try:
            metadata = et.get_metadata(file_path)[0]
            for tag in ['EXIF:DateTimeOriginal', 'EXIF:CreateDate', 'QuickTime:CreateDate', 'File:FileModifyDate']:
                date_str = metadata.get(tag)
                if date_str:
                    try:
                        return datetime.strptime(date_str, "%Y:%m:%d %H:%M:%S")
                    except ValueError:
                        pass  # If parsing fails, try the next tag
        except Exception as e:
            print(f" Error reading metadata for {file_path}: {e}")
        finally:
            time.sleep(0.01)  # Add a small delay to prevent potential ExifTool issues
    
    # If all else fails, use the older of creation or modification time
    try:
        mod_time = datetime.fromtimestamp(os.path.getmtime(file_path))
        create_time = datetime.fromtimestamp(os.path.getctime(file_path))
        return min(mod_time, create_time)
    except Exception as e:
        print(f" Error getting file system times for {file_path}: {e}")
    
    # If even this fails, return None
    return None
    
def get_album_info(album_data):
    root, album_name, segment_name, file_list = album_data
    print(f" Processing album segment: {segment_name}")  # Debug print
    
    album_size = sum(os.path.getsize(os.path.join(root, f)) for f in file_list)
    
    earliest_date = datetime.max
    latest_date = datetime.min
    
    for file in file_list:
        file_path = os.path.join(root, file)
        try:
            date_taken = get_date_taken(file_path)
            if isinstance(date_taken, datetime):
                earliest_date = min(earliest_date, date_taken)
                latest_date = max(latest_date, date_taken)
        except Exception as e:
            print(f" Error getting date for {file_path}: {e}")
    
    if earliest_date == datetime.max:
        earliest_date = latest_date = datetime.now()
    
    print(f" Finished processing album segment: {segment_name}")  # Debug print
    return (album_name, segment_name, album_size, earliest_date, latest_date, len(file_list), root, file_list)

def create_thumbnail(file_path, thumb_path, size=(200, 200)):
    try:
        file_ext = os.path.splitext(file_path)[1].lower()
        
        if file_ext in raw_image_extensions:
            try:
                # First, try using rawpy
                with rawpy.imread(file_path) as raw:
                    rgb = raw.postprocess()
                image = Image.fromarray(rgb)
                image.thumbnail(size)
                image.save(thumb_path, 'JPEG', quality=85)
                print(f"Thumbnail created with rawpy for {file_path}")
                return
            except Exception as e:
                print(f"rawpy failed for {file_path}: {e}")
                
            try:
                # If rawpy fails, try using ffmpeg
                command = [
                    'ffmpeg',
                    '-i', file_path,
                    '-vf', f'scale={size[0]}:{size[1]}:force_original_aspect_ratio=decrease,pad={size[0]}:{size[1]}:(ow-iw)/2:(oh-ih)/2',
                    '-frames:v', '1',
                    '-y',
                    thumb_path
                ]
                subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                print(f"Thumbnail created with ffmpeg for {file_path}")
                return
            except subprocess.CalledProcessError as e:
                print(f"ffmpeg failed for {file_path}: {e}")
        
        elif file_ext in image_extensions:
            with Image.open(file_path) as img:
                # Convert to RGB if the image is in RGBA mode
                if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
                    bg = Image.new('RGB', img.size, (255, 255, 255))
                    bg.paste(img, mask=img.split()[3] if img.mode == 'RGBA' else None)
                    img = bg

                img.thumbnail(size)
                
                # Save as PNG if original is PNG or GIF, otherwise save as JPEG
                if file_ext in ('.png', '.gif'):
                    img.save(thumb_path, 'PNG')
                else:
                    img.save(thumb_path, 'JPEG', quality=85)
            print(f"Thumbnail created with PIL for {file_path}")
            return
            
        elif file_ext in video_extensions or file_ext in raw_video_extensions:
            # Use FFmpeg to create a thumbnail for videos
            command = [
                'ffmpeg',
                '-i', file_path,
                '-ss', '00:00:01.000',
                '-vframes', '1',
                '-vf', f'scale={size[0]}:{size[1]}:force_original_aspect_ratio=decrease,pad={size[0]}:{size[1]}:(ow-iw)/2:(oh-ih)/2',
                '-y',
                thumb_path
            ]
            try:
                result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
                if result.returncode != 0:
                    # FFmpeg failed
                    error_message = result.stderr
                    print(f"\nError creating thumbnail for {file_path}: {error_message}")
                    raise Exception("FFmpeg failed")
            except Exception as e:
                print(f"\nError creating thumbnail for {file_path}: {e}")
                # Create a placeholder thumbnail
                with Image.new('RGB', size, color='red') as img:
                    img.save(thumb_path, 'JPEG')
        else:
            # For other formats, use a placeholder
            with Image.new('RGB', size, color='grey') as img:
                img.save(thumb_path, 'JPEG')
    
    except Exception as e:
        print(f"\nError creating thumbnail for {file_path}: {e}")
        # Create a red placeholder thumbnail in case of any error
        with Image.new('RGB', size, color='red') as img:
            img.save(thumb_path, 'JPEG')
        print(f"Red placeholder created for {file_path} due to error")

 

def create_thumbnail_wrapper(args):
    file_path, thumb_path, size = args
    create_thumbnail(file_path, thumb_path, size)
    return file_path, thumb_path
    
def generate_html_gallery(disc_dir):
    print(f"\nGenerating HTML gallery for {disc_dir}...")
    
    albums = {}
    thumbnail_tasks = []
    total_files = sum(len(files) for _, _, files in os.walk(disc_dir))
    
    with tqdm(total=total_files, desc="Processing files", unit="file") as pbar:
        for root, dirs, files in os.walk(disc_dir):
            if 'thumbs' in dirs:
                dirs.remove('thumbs')
            if 'exiftool_files' in dirs:
                dirs.remove('exiftool_files')
            if 'ignore' in dirs:
                dirs.remove('ignore')
            
            for file in files:
                if file.endswith('.html'):
                    pbar.update(1)
                    continue
                
                file_ext = os.path.splitext(file)[1].lower()
                if file_ext in image_extensions or file_ext in video_extensions or file_ext in raw_video_extensions:
                    try:
                        file_path = os.path.join(root, file)
                        relative_path = os.path.relpath(file_path, disc_dir)
                        album_name = os.path.relpath(root, disc_dir).split(os.sep)[0]
                        
                        thumb_dir = os.path.join(root, 'thumbs')
                        os.makedirs(thumb_dir, exist_ok=True)
                        thumb_path = os.path.join(thumb_dir, f"{os.path.splitext(file)[0]}.jpg")
                        
                        thumbnail_tasks.append((file_path, thumb_path, (200, 200)))
                        
                        file_type = "image" if file_ext in image_extensions else "video"
                        if album_name not in albums:
                            albums[album_name] = []
                        albums[album_name].append((relative_path.replace(os.sep, "/"), os.path.relpath(thumb_path, disc_dir).replace(os.sep, "/"), file, file_type))
                    except Exception as e:
                        print(f" Error processing {file_path}: {e}")
                pbar.update(1)
    
    print("Generating thumbnails...")
    with multiprocessing.Pool(processes=getCPUs()) as pool:
        list(tqdm(pool.imap_unordered(create_thumbnail_wrapper, thumbnail_tasks), total=len(thumbnail_tasks), desc="Creating thumbnails", unit="thumbnail"))
    
    print("Generating HTML content...")
    html = generate_html_structure(albums)
    
    print("Writing HTML file...")
    with open(os.path.join(disc_dir, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"HTML gallery generated for {disc_dir}")

def generate_html_structure(albums):
    html = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="icon" type="image/svg+xml" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><rect x='10' y='10' width='80' height='80' rx='10' fill='%234a90e2'/><circle cx='50' cy='50' r='30' fill='%23f5a623'/><path d='M50 20 L80 50 L50 80 L20 50 Z' fill='%23fff'/></svg>" />
    <title>Media Gallery</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            line-height: 1.6;
            margin: 0;
            padding: 20px;
            background-color: #f4f4f4;
        }
        .album {
            background-color: #fff;
            border-radius: 5px;
            box-shadow: 0 2px 5px rgba(0,0,0,0.1);
            margin-bottom: 20px;
            overflow: hidden;
        }
        .album h2 {
            background-color: #007bff;
            color: #fff;
            padding: 10px;
            margin: 0;
        }
        .album-content {
            padding: 15px;
        }
        .thumbnail-container {
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
        }
        .thumbnail {
            width: 150px;
            height: 150px;
            object-fit: cover;
            cursor: pointer;
            transition: transform 0.3s ease;
        }
        .thumbnail:hover {
            transform: scale(1.05);
        }
        .expand-btn {
            background-color: #28a745;
            color: #fff;
            border: none;
            padding: 5px 10px;
            cursor: pointer;
            margin-top: 10px;
        }
        .expand-btn:hover {
            background-color: #218838;
        }
        .hidden {
            display: none !important;
        }
        .thumbnail-wrapper {
            position: relative;
            display: inline-block;
        }
        .file-type-icon {
            position: absolute;
            bottom: 5px;
            right: 5px;
            background-color: rgba(0, 0, 0, 0.7);
            color: white;
            padding: 2px 5px;
            font-size: 12px;
            border-radius: 3px;
        }
        .modal {
            display: none;
            position: fixed;
            z-index: 1000;
            left: 0;
            top: 0;
            width: 100%;
            height: 100%;
            overflow: auto;
            background-color: rgba(0,0,0,0.9);
        }
        .modal-content {
            display: flex;
            justify-content: center;
            align-items: center;
            height: 100%;
            padding: 20px;
            box-sizing: border-box;
            user-select: none;
        }
        .modal-content img {
            max-width: 90%;
            max-height: 90vh;
            object-fit: contain;
        }
        .modal-content video {
            max-width: 90%;
            max-height: 90vh;
        }
        .close {
            position: absolute;
            top: 15px;
            right: 35px;
            color: #f1f1f1;
            font-size: 40px;
            font-weight: bold;
            transition: 0.3s;
        }
        .close:hover,
        .close:focus {
            color: #bbb;
            text-decoration: none;
            cursor: pointer;
        }
        .nav-button {
            position: absolute;
            top: 50%;
            background-color: rgba(0,0,0,0.5);
            border: none;
            color: white;
            font-size: 36px;
            padding: 10px;
            cursor: pointer;
            border-radius: 50%;
            user-select: none;
        }

        #prevButton {
            left: 20px;
        }

        #nextButton {
            right: 20px;
        }
    </style>
</head>
<body>
    <h1>Media Gallery</h1>
    """
    for album_name, files in albums.items():
        encoded_album_name = urllib.parse.quote(album_name)
        html += f"""
    <div class="album">
        <h2>{album_name}</h2>
        <div class="album-content">
            <div class="thumbnail-container">
        """
        
        for i, (file_path, thumb_path, file_name, file_type) in enumerate(files):
            hidden_class = ' hidden' if i >= 5 else ''
            file_ext = os.path.splitext(file_name)[1].lower()
            icon_text = 'Video' if file_ext in video_extensions or file_ext in raw_video_extensions else file_ext[1:].upper()
            
            encoded_file_path = urllib.parse.quote(file_path)
            encoded_thumb_path = urllib.parse.quote(thumb_path)
            encoded_file_name = urllib.parse.quote(file_name)
            
            html += f"""
                <div class="thumbnail-wrapper{hidden_class}">
                    <a href="{encoded_file_path}" class="thumbnail-link" data-type="{file_type}">
                        <img class="thumbnail" data-src="{encoded_thumb_path}" alt="{encoded_file_name}" title="{file_type}: {encoded_file_name}">
                        <span class="file-type-icon">{icon_text}</span>
                    </a>
                </div>
            """
        
        html += """
            </div>
        """
        
        if len(files) > 5:
            html += """
            <button class="expand-btn">Show More</button>
            """
        
        html += """
        </div>
    </div>
        """

    html += """
    <div id="mediaModal" class="modal">
        <span class="close">&times;</span>
        <div class="modal-content">
            <button class="nav-button" id="prevButton">&#10094;</button>
            <img id="modalImage" src="" style="display:none;">
            <video id="modalVideo" controls style="display:none;">
                <source id="modalVideoSource" src="" type="">
                Your browser does not support the video tag.
            </video>
            <div id="modalMessage" style="display:none; color: white; text-align: center;">
                This video format is not supported by your browser.
                <a id="modalDownloadLink" href="" style="color: #0af;">Click here to download the video.</a>
            </div>
            <button class="nav-button" id="nextButton">&#10095;</button>
        </div>
    </div>
    <script>
        var mediaItems = [];
        var currentIndex = -1;
        // Lazy loading
        document.addEventListener("DOMContentLoaded", function() {
            var lazyImages = [].slice.call(document.querySelectorAll("img.thumbnail"));

            if ("IntersectionObserver" in window) {
                let lazyImageObserver = new IntersectionObserver(function(entries, observer) {
                    entries.forEach(function(entry) {
                        if (entry.isIntersecting) {
                            let lazyImage = entry.target;
                            lazyImage.src = lazyImage.dataset.src;
                            lazyImage.classList.remove("lazy");
                            lazyImageObserver.unobserve(lazyImage);
                        }
                    });
                });

                lazyImages.forEach(function(lazyImage) {
                    lazyImageObserver.observe(lazyImage);
                });
            } else {
                // Fallback for browsers that don't support IntersectionObserver
                let active = false;

                const lazyLoad = function() {
                    if (active === false) {
                        active = true;

                        setTimeout(function() {
                            lazyImages.forEach(function(lazyImage) {
                                if ((lazyImage.getBoundingClientRect().top <= window.innerHeight && lazyImage.getBoundingClientRect().bottom >= 0) && getComputedStyle(lazyImage).display !== "none") {
                                    lazyImage.src = lazyImage.dataset.src;
                                    lazyImage.classList.remove("lazy");

                                    lazyImages = lazyImages.filter(function(image) {
                                        return image !== lazyImage;
                                    });

                                    if (lazyImages.length === 0) {
                                        document.removeEventListener("scroll", lazyLoad);
                                        window.removeEventListener("resize", lazyLoad);
                                        window.removeEventListener("orientationchange", lazyLoad);
                                    }
                                }
                            });

                            active = false;
                        }, 200);
                    }
                };

                document.addEventListener("scroll", lazyLoad);
                window.addEventListener("resize", lazyLoad);
                window.addEventListener("orientationchange", lazyLoad);
            }
            
            var thumbnails = document.querySelectorAll(".thumbnail-link");
            thumbnails.forEach(function(thumb, index) {
                mediaItems.push({
                    src: thumb.getAttribute('href'),
                    type: thumb.getAttribute('data-type'),
                    fileExt: thumb.getAttribute('href').split('.').pop().toLowerCase()
                });
                // Store index as a data attribute for easy access
                thumb.dataset.index = index;
            });
            
            var expandButtons = document.querySelectorAll(".expand-btn");
            expandButtons.forEach(function(button) {
                button.addEventListener("click", function() {
                    var album = this.closest(".album");
                    var hiddenThumbnails = album.querySelectorAll(".thumbnail-wrapper.hidden");
                    hiddenThumbnails.forEach(function(thumbnail) {
                        thumbnail.classList.remove("hidden");
                    });
                    this.style.display = "none";
                });
            });
        });
		// Modal functionality
        var modal = document.getElementById('mediaModal');
        var modalImg = document.getElementById("modalImage");
        var modalVideo = document.getElementById("modalVideo");
        var modalVideoSource = document.getElementById("modalVideoSource");
        var modalMessage = document.getElementById('modalMessage');
        var downloadLink = document.getElementById('modalDownloadLink');
        var closeBtn = document.getElementsByClassName("close")[0];
        var prevButton = document.getElementById('prevButton');
        var nextButton = document.getElementById('nextButton');

        function showMedia(index) {
            if (index < 0 || index >= mediaItems.length) {
                return;
            }
            currentIndex = index;
            var item = mediaItems[index];
            var src = item.src;
            var type = item.type;
            var fileExt = item.fileExt;
            var mimeType = '';

            if (type === "image") {
                modalImg.src = src;
                modalImg.style.display = "block";
                modalVideo.style.display = "none";
                modalMessage.style.display = "none";
            } else if (type === "video") {
                // Determine MIME type based on file extension
                if (fileExt === 'mp4') {
                    mimeType = 'video/mp4';
                } else if (fileExt === 'webm') {
                    mimeType = 'video/webm';
                } else if (fileExt === 'ogg' || fileExt === 'ogv') {
                    mimeType = 'video/ogg';
                } else {
                    mimeType = '';
                }

                modalVideoSource.src = src;
                modalVideoSource.type = mimeType;
                modalVideo.load();

                if (mimeType) {
                    modalVideo.style.display = "block";
                    modalImg.style.display = "none";
                    modalMessage.style.display = "none";
                } else {
                    // Unsupported video format
                    modalVideo.style.display = "none";
                    modalImg.style.display = "none";
                    modalMessage.style.display = "block";
                    downloadLink.href = src;
                }
            }
            modal.style.display = "block";
        }

        // Click event for thumbnails
        document.addEventListener('click', function(e) {
            if (e.target && e.target.classList.contains('thumbnail')) {
                var link = e.target.closest('.thumbnail-link');
                var index = parseInt(link.dataset.index);
                showMedia(index);
                e.preventDefault();
                return false;
            } else if (e.target && e.target.classList.contains('modal-content')) {
				modal.style.display = "none";
				modalVideo.pause();
				modalVideo.style.display = "none";
				modalImg.style.display = "none";
				modalMessage.style.display = "none";
			}
        });

        // Next and Previous button functionality
        nextButton.onclick = function() {
            if (currentIndex + 1 < mediaItems.length) {
                showMedia(currentIndex + 1);
            }
        };

        prevButton.onclick = function() {
            if (currentIndex - 1 >= 0) {
                showMedia(currentIndex - 1);
            }
        };

        // Close button functionality
        closeBtn.onclick = function() {
            modal.style.display = "none";
            modalVideo.pause();
            modalVideo.style.display = "none";
            modalImg.style.display = "none";
            modalMessage.style.display = "none";
        };

        window.onclick = function(event) {
            if (event.target == modal) {
                modal.style.display = "none";
                modalVideo.pause();
                modalVideo.style.display = "none";
                modalImg.style.display = "none";
                modalMessage.style.display = "none";
            } 
        };

        // Keyboard navigation
        document.addEventListener('keydown', function(e) {
            if (modal.style.display === "block") {
                if (e.key === 'ArrowRight' || e.key === 'Right') {
                    nextButton.onclick();
                } else if (e.key === 'ArrowLeft' || e.key === 'Left') {
                    prevButton.onclick();
                } else if (e.key === 'Escape' || e.key === 'Esc') {
                    closeBtn.onclick();
                }
            }
        });
    </script>
</body>
</html>
    """
    
    return html



def process_file(args):
    global source_dir_global, dest_dir_global, move_files_global, file_hashes, log_lock
    file_info, current_disc_dir, log_file = args
    source_album_name, dest_album_name, file_path, file_size = file_info
    
    try:
        # Construct the source path
        source_path = os.path.join(source_dir_global, source_album_name, file_path)
        
        # Construct the destination path, using the new album name (which might include disc number)
        dest_path = os.path.join(current_disc_dir, dest_album_name, file_path)
        
        logging.debug(f"Processing file: {source_path} -> {dest_path}")

        if not os.path.exists(source_path):
            logging.error(f"Source file does not exist: {source_path}")
            return None, 0, None, f"Source file does not exist: {source_path}"

        if not os.access(source_path, os.R_OK):
            logging.error(f"No read permission for source file: {source_path}")
            return None, 0, None, f"No read permission for source file: {source_path}"

        # Create the full path for the destination, including album folder
        dest_dir = os.path.dirname(dest_path)
        if not os.path.exists(dest_dir):
            try:
                os.makedirs(dest_dir, exist_ok=True)
                logging.debug(f"Created destination directory: {dest_dir}")
            except Exception as e:
                logging.error(f"Failed to create destination directory {dest_dir}: {str(e)}")
                return None, 0, None, f"Failed to create destination directory {dest_dir}: {str(e)}"
        elif not os.access(dest_dir, os.W_OK):
            logging.error(f"No write permission for destination directory: {dest_dir}")
            return None, 0, None, f"No write permission for destination directory: {dest_dir}"

        # Check available space (for POSIX systems)
        if os.name == 'posix':
            stats = os.statvfs(dest_dir)
            available_space = stats.f_frsize * stats.f_bavail
            if file_size > available_space:
                logging.error(f"Not enough disk space to copy {source_path}. Required: {file_size}, Available: {available_space}")
                return None, 0, None, f"Not enough disk space to copy {source_path}. Required: {file_size}, Available: {available_space}"

        # Perform the copy or move operation
        try:
            if move_files_global:
                logging.debug(f"Moving file: {source_path} -> {dest_path}")
                shutil.move(source_path, dest_path)
            else:
                logging.debug(f"Copying file: {source_path} -> {dest_path}")
                shutil.copy2(source_path, dest_path)
        except Exception as e:
            logging.error(f"{'Move' if move_files_global else 'Copy'} operation failed for {source_path}: {str(e)}")
            return None, 0, None, f"{'Move' if move_files_global else 'Copy'} operation failed for {source_path}: {str(e)}"

        # Verify the file was actually copied/moved
        if not os.path.exists(dest_path):
            logging.error(f"File was not {'moved' if move_files_global else 'copied'} to destination: {dest_path}")
            return None, 0, None, f"File was not {'moved' if move_files_global else 'copied'} to destination: {dest_path}"

        # Log successful operation
        with log_lock:
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(f"Successfully {'moved' if move_files_global else 'copied'}: {source_path} -> {dest_path}\n")
        
        logging.debug(f"Successfully processed file: {source_path} -> {dest_path}")
        return source_path, file_size, dest_path, None
    except Exception as e:
        error_msg = f"Unexpected error processing {source_path}: {str(e)}\n{traceback.format_exc()}"
        logging.error(error_msg)
        return None, 0, None, error_msg
        
def calculate_similarity(album1, album2):
    date1 = album1[2]
    date2 = album2[2]
    return abs((date1 - date2).days)

def optimize_disc_packing(albums, max_size, min_fill_ratio=0.9):
    optimized_discs = []
    current_disc = []
    current_size = 0
    
    # Convert albums to a list of (total_size, album_name, segment_name, structure) tuples
    with Pool(processes=getCPUs()) as pool:
        album_structures = list(tqdm(pool.imap(get_album_structure, albums), total=len(albums), desc="Analyzing albums"))
    
    album_heap = [(-total_size, album_name, segment_name, structure) for album_name, segment_name, structure, total_size in album_structures]
    heapq.heapify(album_heap)
    
    while album_heap:
        _, album_name, segment_name, structure = heapq.heappop(album_heap)
        
        for year_month, files in sorted(structure.items(), key=lambda x: sum(f[1] for f in x[1]), reverse=True):
            files.sort(key=lambda x: x[1], reverse=True)
            
            while files:
                file_path, file_size = files.pop(0)
                
                if file_size > max_size:
                    print(f"Warning: File {file_path} in album {album_name} exceeds max disc size. Skipping.")
                    continue
                
                if current_size + file_size <= max_size:
                    current_disc.append((album_name, segment_name, file_path, file_size))
                    current_size += file_size
                else:
                    if current_size / max_size >= min_fill_ratio or not current_disc:
                        optimized_discs.append(current_disc)
                        current_disc = [(album_name, segment_name, file_path, file_size)]
                        current_size = file_size
                    else:
                        # Try to find a smaller file that fits
                        found_smaller = False
                        for i, (small_file_path, small_file_size) in enumerate(files):
                            if current_size + small_file_size <= max_size:
                                current_disc.append((album_name, segment_name, small_file_path, small_file_size))
                                current_size += small_file_size
                                files.pop(i)
                                found_smaller = True
                                break
                        
                        if not found_smaller:
                            optimized_discs.append(current_disc)
                            current_disc = [(album_name, segment_name, file_path, file_size)]
                            current_size = file_size
            
            # Update the album heap with remaining files
            remaining_size = sum(f[1] for f in files)
            if remaining_size > 0:
                heapq.heappush(album_heap, (-remaining_size, album_name, segment_name, {year_month: files}))
        
        # Try to fill remaining space with files from other albums
        while album_heap and current_size < max_size * min_fill_ratio:
            next_album = album_heap[0]
            next_album_name = next_album[1]
            next_segment_name = next_album[2]
            next_album_structure = next_album[3]
            next_year_month = next(iter(next_album_structure))
            next_files = next_album_structure[next_year_month]
            
            filled = False
            for i, (file_path, file_size) in enumerate(next_files):
                if current_size + file_size <= max_size:
                    current_disc.append((next_album_name, next_segment_name, file_path, file_size))
                    current_size += file_size
                    next_files.pop(i)
                    filled = True
                    break
            
            if not filled:
                break
            
            if not next_files:
                heapq.heappop(album_heap)
            else:
                heapq.heapreplace(album_heap, (-sum(f[1] for f in next_files), next_album_name, next_segment_name, {next_year_month: next_files}))
    
    if current_disc:
        optimized_discs.append(current_disc)
    
    return optimized_discs


    
def cleanup():
    global et
    if et:
        try:
            et.terminate()
        except:
            pass
        finally:
            et = None
    try:
        exiftool.ExifToolHelper.terminate()
    except:
        pass
    time.sleep(0.1)
    print("Cleanup complete. All exiftool processes have been terminated.")
    
def getCPUs(n=1):
    return max(1,multiprocessing.cpu_count()-n) # we keep one core free for the system/user, to prevent thrashing
    
def organize_media(source_dir, dest_dir, move_files=False, max_size=23.2 * 1024 * 1024 * 1024):
    global source_dir_global, dest_dir_global, move_files_global, file_hashes
    source_dir_global = os.path.abspath(source_dir)
    dest_dir_global = os.path.abspath(dest_dir)
    move_files_global = move_files

    manager = Manager()
    processed_counter = Value('i', 0)
    current_disc = Value('i', 1)
    file_hashes = manager.dict()
    log_lock = Lock()

    log_file = os.path.join(dest_dir_global, 'processed_files.log')
    
    try:
        print(f"Scanning directories... Using {getCPUs(0)} CPUs")
        segmented_albums = get_segmented_albums(source_dir_global)
        albums = []
        
        with ProcessPoolExecutor(max_workers=getCPUs(0)) as executor:
            future_to_album = {executor.submit(get_album_info, album_data): album_data for album_data in segmented_albums}
            
            for future in tqdm(as_completed(future_to_album), total=len(segmented_albums), desc="Processing album segments"):
                album = future.result()
                if album is not None:
                    albums.append(album)

        print("Packing discs...")
        optimized_discs = optimize_disc_packing(albums, max_size)

        for disc_index, disc in enumerate(optimized_discs, start=1):
            current_disc_dir = os.path.join(dest_dir_global, f"Disc_{disc_index}")
            os.makedirs(current_disc_dir, exist_ok=True)
            
            disc_size = sum(file_size for _, _, _, file_size in disc)
            print(f"Packing Disc_{disc_index}: {disc_size / (1024*1024*1024):.2f} GB / {max_size / (1024*1024*1024):.2f} GB")
            
            with ProcessPoolExecutor(max_workers=getCPUs(),
                                     initializer=init_worker,
                                     initargs=(source_dir_global, dest_dir_global, move_files, file_hashes, log_lock)) as executor:
                results = list(tqdm(
                    executor.map(process_file, [(file_info, current_disc_dir, log_file) for file_info in disc]),
                    total=len(disc),
                    desc=f"Processing Disc_{disc_index}",
                    unit="file"
                ))
            
            processed_subdirs = set()
            successful_copies = 0
            errors = []

            for result in results:
                source_path, _, dest_path, error_msg = result
                if error_msg:
                    errors.append(error_msg)
                    print(f"Error processing a file in Disc_{disc_index}: {error_msg}")
                elif source_path and dest_path:
                    if os.path.exists(dest_path):
                        with processed_counter.get_lock():
                            processed_counter.value += 1
                        processed_subdirs.add(os.path.dirname(dest_path))
                        successful_copies += 1
                    else:
                        print(f"Warning: File not found at destination after processing: {dest_path}")
            
            print(f"Successfully processed {successful_copies} out of {len(disc)} files for Disc_{disc_index}")
            if errors:
                print(f"Encountered {len(errors)} errors while processing Disc_{disc_index}")
                error_log_path = os.path.join(dest_dir_global, f"error_log_disc_{disc_index}.txt")
                with open(error_log_path, 'w', encoding='utf-8') as error_log:
                    for error in errors:
                        error_log.write(f"{error}\n")
                print(f"Detailed error log written to: {error_log_path}")
            
            print(f"Successfully processed {successful_copies} out of {len(disc)} files for Disc_{disc_index}")
            
            print("Creating hash manifests...")
            for subdir in processed_subdirs:
                create_manifest_file(subdir)

            generate_html_gallery(current_disc_dir)
            
            with current_disc.get_lock():
                current_disc.value += 1

    except Exception as E:
        exc_type, exc_obj, exc_tb = sys.exc_info()
        line_number = exc_tb.tb_lineno
        print(f"Error on line {line_number}: {E}")
    finally:
        cleanup()

    print(f"\nOrganized media files into {current_disc.value - 1} discs and generated HTML galleries.")
    print(f"Files were {'moved' if move_files else 'copied'} to the destination.")
    print(f"Total files processed: {processed_counter.value}")
    print("Hash manifests created for each subdirectory.")

if __name__ == "__main__":
    if len(sys.argv) < 3 or len(sys.argv) > 4:
        print("Usage: python script.py <source_directory> <destination_directory> [--move]")
        sys.exit(1)

    source_directory = sys.argv[1]
    destination_directory = sys.argv[2]
    move_files = "--move" in sys.argv

    if not os.path.exists(source_directory):
        print(f"Error: Source directory '{source_directory}' does not exist.")
        sys.exit(1)
        
    os.makedirs(destination_directory, exist_ok=True)

    try:
        organize_media(source_directory, destination_directory, move_files)
    except KeyboardInterrupt:
        print("\nScript interrupted by user. Cleaning up...")
    except Exception as E:
        exc_type, exc_obj, exc_tb = sys.exc_info()
        line_number = exc_tb.tb_lineno
        print(f"Error on line {line_number}: {E}")
    finally:
        cleanup()
