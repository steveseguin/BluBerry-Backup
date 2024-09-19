# Media Organizer and Gallery Generator

This Python script organizes media files (images and videos) from a source directory into optimized "discs" based on a specified maximum size. It also generates HTML galleries for each disc with features like thumbnail generation, slideshows, and more.

## Features

- **Media Organization**: Packs media files into discs without exceeding the maximum size.
- **Album Segmentation**: Splits large albums into smaller segments to fit disc constraints.
- **Optimized for Google Photos exports**: Uses and stores the meta data available within Google Takeout photo exports.
- **Thumbnail Generation**: Creates thumbnails for images and videos, including support for RAW image formats.
- **HTML Gallery Generation**: Generates an interactive HTML gallery for each disc with:
  - Lazy loading of images.
  - Modal view with slideshow functionality.
  - Next and previous navigation.
  - Keyboard navigation support.
  - Video playback with fallback for unsupported formats.
- **Hash Manifest Creation**: Generates a `hash_manifest.json` file for each directory for integrity checks.

<p align="center">
  <img src="https://github.com/user-attachments/assets/8631c85f-1b43-476f-bc77-81626e856aa8" height="200px" />
  <img src="https://github.com/user-attachments/assets/ad5099f5-f57e-495c-afc2-fd76348783ac" height="200px" />
  <img src="https://github.com/user-attachments/assets/5fdb3991-de7f-475e-a04d-f105d515860e" height="200px" />
</p>

## Requirements

- **Python 3.6+**
- **Operating System**: Windows, macOS, or Linux

### Python Packages

Install the required Python packages using:

```
pip install -r requirements.txt
```

The required packages are:

- `Pillow`
- `rawpy`
- `tqdm`
- `exiftool`

### External Dependencies

- **FFmpeg**: Used for video thumbnail generation and as a fallback for RAW image thumbnails.
- **ExifTool**: Used for extracting metadata from media files.

Ensure that both `ffmpeg` and `exiftool` are installed and added to your system's PATH so that they can be called from the command line. If on windows, they should be called `ffmpeg.exe` and `exiftool.exe`, and I just include them in the same folder as the Python script we intend to run.

![image](https://github.com/user-attachments/assets/06acb7b2-71e0-4612-a350-23121087d347)

#### Installing FFmpeg

- **Windows**: Download from [ffmpeg.org](https://ffmpeg.org/download.html#build-windows) and add the `bin` folder to your PATH.
- **macOS**: Install via Homebrew:

  ```
  brew install ffmpeg
  ```

- **Linux**: Install via package manager:

  ```
  sudo apt-get install ffmpeg
  ```

#### Installing ExifTool

- **Windows**: Download from [exiftool.org](https://exiftool.org/) and add the executable to your PATH.
- **macOS**: Install via Homebrew:

  ```
  brew install exiftool
  ```

- **Linux**: Install via package manager:

  ```
  sudo apt-get install libimage-exiftool-perl
  ```

## Usage

```
python script.py <source_directory> <destination_directory> [--move]
```

- `<source_directory>`: The path to the directory containing your media files.
- `<destination_directory>`: The path where you want the organized discs and galleries to be created.
- `--move` (optional): If specified, files will be moved instead of copied.

### Example

```bash
python script.py /path/to/source /path/to/destination
```

### Notes

- The script will create subdirectories named `Disc_1`, `Disc_2`, etc., in the destination directory.
- Each disc will contain media files organized into albums, along with an `index.html` file for the gallery.
- A `processed_files.log` file will be created in the destination directory, logging all successful operations.
- If errors occur, error logs like `error_log_disc_1.txt` will be generated in the destination directory.

## How It Works

1. **Scanning and Segmentation**:
   - The script scans the source directory for media files.
   - Albums with more than 300 files are segmented into smaller albums to fit disc constraints.

2. **Metadata Extraction**:
   - Uses `exiftool` to extract date taken and other metadata from media files.
   - If metadata is unavailable, falls back to file system timestamps.

3. **Disc Packing Optimization**:
   - Organizes media files into discs without exceeding the maximum size
     - The default packing size targets 23.2 GB, which will safely fill a standard 25 GB BD-R disc.
   - Prioritizes filling discs to at least 90% capacity, so not to split albums too aggressively

4. **File Processing**:
   - Copies or moves files from the source to the destination discs.
   - Preserves the directory structure and album organization.

5. **Thumbnail Generation**:
   - Generates thumbnails for images and videos.
   - Supports RAW image formats using `rawpy` or `ffmpeg` as a fallback.
   - Creates placeholder thumbnails if thumbnail generation fails.

6. **HTML Gallery Generation**:
   - Creates an `index.html` file for each disc with an interactive gallery.
   - Features include lazy loading, modal pop-ups, slideshows, and keyboard navigation.

7. **Hash Manifest Creation**:
   - Generates a `hash_manifest.json` file in each album directory.
   - Useful for verifying file integrity.

## Customization

- **Adjusting Disc Size**: Modify the `max_size` parameter in the `organize_media` function call to change the maximum disc size.
- **Changing Files Per Segment**: Adjust the `files_per_segment` parameter in the `get_segmented_albums` function to change how albums are segmented.
- **Excluding Files or Folders**: Update the `skip_files` set and the conditions in the `get_segmented_albums` function to exclude specific files or folders.

## Troubleshooting

- **Missing Thumbnails**:
  - Ensure `ffmpeg` is correctly installed and accessible.
  - Check for any error messages during thumbnail generation.

- **Videos Not Playing in Browser**:
  - Some browsers may not support certain video formats (e.g., `.avi`).
  - The gallery provides a download link for unsupported formats.

- **Performance Issues**:
  - Thumbnail generation can be resource-intensive.
  - Running the script on a machine with adequate resources is recommended.

## Contributing

Contributions are welcome! Please open an issue or submit a pull request for any improvements or bug fixes.

## License

This project is licensed under the MIT License.

I hope this displays correctly now. You can copy the content within the code blocks directly into your `README.md` and `requirements.txt` files.

Let me know if you have any questions or need further assistance!
