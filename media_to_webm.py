from contextlib import suppress
from math import floor
from mutagen import File
from operator import floordiv, mul
from os import unlink, path
from subprocess import run
from sys import argv
from time import sleep
from traceback import print_exc
from typing import Any

# Config
FFMPEG_PATH = "ffmpeg" # If not in system PATH, use filepath of ffmpeg.exe
DEFAULT_BITRATE = 256
AUDIO_FILETYPES = ('flac', 'mp3', 'm4a', 'aac', 'alac', 'ogg', 'wav')

# Colour text using Colorama
USE_COLOURS = True

# Resize image inputs using Wand or Pillow
RESIZE_IMAGE = True
IMAGE_MIN = 400
IMAGE_MAX = 800

# Force use first embedded MP3 image
FIRST_IMAGE = True

# Constants
TO_BITS = 8
KILOBIT = 1000
MEGABYTE = 1024*1024
DURATION_BYTE_MARKER = b'\x44\x89'
FIVE_MINUTE_DURATION = b'\x44\x89\x88\x41\x12\x4F\x80'
MIN_BITRATE = 45 # Minimum allowed libvorbis bitrate

# 4chan limits
MAX_FILE_SIZE = 6 * MEGABYTE
MAX_LENGTH = 300

if USE_COLOURS:
	import colorama
	WHITE = colorama.Fore.LIGHTWHITE_EX
	YELLOW = colorama.Fore.YELLOW
	RED = colorama.Fore.RED
else:
	WHITE = ''
	YELLOW = ''
	RED = ''

try:
	from wand.image import Image
	image_library = 'wand'
except ImportError:
	try:
		from PIL import Image
		image_library = 'pillow'
	except ImportError:
		RESIZE_IMAGE = False

def warning(text: str) -> None:
	print(YELLOW+text+WHITE)

def error(text: str) -> None:
	print(RED+text+WHITE)

def has_tags(file_tags: dict[str: Any], *search_tags) -> bool:
	return all(tag in file_tags for tag in search_tags)

def die() -> None:
	if USE_COLOURS:
		colorama.deinit()
	quit()

def get_length(file: str) -> int:
	try:
		file_info = File(file)
		return file_info.info.length
	except AttributeError:
		while True:
			l = input("Length (in seconds): ")
			with suppress(ValueError):
				return int(l)

def get_bitrate(length: int) -> int:
	if length * DEFAULT_BITRATE * KILOBIT > MAX_FILE_SIZE * TO_BITS:
		compression_multiplier = 1 + (0.001*length + 0.2)*length/9800 # Actual file is smaller than the simple calculation assumes, this ups the bitrate to compensate for that
		bitrate = floor(MAX_FILE_SIZE * TO_BITS / (length * KILOBIT))
		if bitrate * compression_multiplier < DEFAULT_BITRATE:
			bitrate = floor(bitrate * compression_multiplier)
		if bitrate < MIN_BITRATE:
			bitrate = MIN_BITRATE
			warning("Bitrate at minimum, file may be too large")
		return bitrate
	return DEFAULT_BITRATE

def get_title(file: str) -> str:
	audio_file = File(file)
	if {'audio/mp3', 'audio/wav'} & set(audio_file.mime):
		if has_tags(audio_file.tags, 'TIT2', 'TPE1'):
			return f"{audio_file.tags['TPE1']} - {audio_file.tags['TIT2']}".replace('"', '\\"')
	elif 'audio/flac' in audio_file.mime:
		if has_tags(audio_file, 'title', 'artist'):
			return f"{audio_file.tags['artist'][0]} - {audio_file.tags['title'][0]}".replace('"', '\\"')
	elif 'audio/m4a' in audio_file.mime:
		if has_tags(audio_file.tags, '©nam', '©ART'):
			return f"{audio_file.tags['©ART']} - {audio_file.tags['©nam']}".replace('"', '\\"')
	return path.splitext(path.split(file)[1])[0].replace('"', '\\"')

def handle_large_webm(webm: str, files: list[str], bitrate: int, title: str) -> None:
	with open(webm, 'rb') as file:
		data = bytearray(file.read())

	if len(data) > MAX_FILE_SIZE:
		if bitrate == MIN_BITRATE:
			error("Output too large, minimum bitrate already used")
		else:
			new_bitrate = floor(bitrate * MAX_FILE_SIZE / len(data))
			if new_bitrate < 45:
				bitrate = MIN_BITRATE
			elif bitrate == new_bitrate:
				new_bitrate -= 1 # Just in case, might not actually get used ever. Better than an infinite loop though
			warning(f"Retrying conversion with lower bitrate ({bitrate} → {new_bitrate})")
			unlink(webm)
			convert_to_webm(webm, files, new_bitrate, title)

def convert_to_webm(webm: str, files: list[str], bitrate: int, title: str) -> None:
	command = FFMPEG_PATH
	command += ''.join(f' -i "{filepath}"' for filepath in files)
	command += f' -c:v libvpx -c:a libvorbis -b:a {bitrate}k -metadata title="{title}" "{webm}"'
	run(command, shell=True, capture_output=True)
	with open(webm, 'rb') as file:
		data = bytearray(file.read())
	if len(data) > MAX_FILE_SIZE:
		handle_large_webm(webm, files, bitrate, title)

def scale(image: Image, side_length: int) -> tuple[int, int]:
	factor = 1
	score = 1000000
	if side_length > IMAGE_MAX:
		operator = floordiv
	else:
		operator = mul
	while True:
		if IMAGE_MIN < (side_length // (factor+1)) < IMAGE_MAX:
			new_score = abs((IMAGE_MAX + IMAGE_MIN)//2 - operator(side_length, factor+1))
			if new_score > score:
				break
			score = new_score
		elif IMAGE_MIN > operator(side_length, factor+1):
			break
		factor += 1
	new_size = (operator(image.width, factor), operator(image.height, factor))
	if image_library == 'wand':
		image.resize(*new_size, 'cubic')
		image.adaptive_sharpen(4, 0.8)
	else:
		image.resize(new_size)
	return new_size

def resize(image: Image, image_file: str, side_length: int) -> None:
	filename, extension = path.splitext(image_file)
	new_filepath = f"{filename}-resized{extension}"
	width, height = scale(image, side_length)
	print(f"Resized image to {width}x{height}")
	if image_library == 'wand':
		image.save(filename=new_filepath)
	else:
		image.save(new_filepath)

def check_resize(filepath: str) -> bool:
	if image_library == 'wand':
		i = Image(filename=filepath)
	else:
		i = Image.open(filepath)
	side_length = max(i.size)
	if side_length <= IMAGE_MAX:
		return False
	resize(i, filepath, side_length)
	return True

def no_embedded_image() -> bool:
	while (answer := input('No embedded image found. Continue anyway? [y/n] ').lower()) not in ('y', 'n'):
		pass
	if answer == 'y':
		return False
	else:
		die()

def check_resize_embedded(file: str) -> str|bool:
	audio_file = File(file)
	if {'audio/mp3', 'audio/wav'} & set(audio_file.mime):
		image = audio_file.tags.get('APIC:')
		if image is None:
			return no_embedded_image()
		image = image.data
	elif 'audio/flac' in audio_file.mime:
		if not audio_file.pictures:
			return no_embedded_image()
		image = audio_file.pictures[0].data
	elif 'audio/aac' in audio_file.mime:
		image = audio_file.tags.get('covr')
		if image is None:
			return no_embedded_image()
	else:
		return no_embedded_image()
	
	image = Image(blob=image)
	extension = image.mimetype.split('/')[1]
	side_length = max(image.size)
	if side_length > IMAGE_MAX:
		filename = path.splitext(file)[0]
		resize(image, f'{filename}-image.{extension}', side_length)
		return f'{filename}-image-resized.{extension}'
	elif FIRST_IMAGE and path.splitext(file)[1] == '.mp3' and 'APIC: ' in audio_file.tags:
		filename = path.splitext(file)[0]
		if image_library == 'wand':
			image.save(filename=f'{filename}-image.{extension}')
		else:
			image.save(f'{filename}-image.{extension}')
		return f'{filename}-image.{extension}'
	return False

if __name__ == '__main__':
	if USE_COLOURS:
		colorama.init()
		print(WHITE, end='')

	try:
		if not len(argv) in {2, 3}:
			die()

		files = argv[1:]
		if len(files) == 1:
			original_file = files[0]
		else:
			if files[0].endswith(AUDIO_FILETYPES):
				original_file, image_file = files
			else:
				image_file, original_file = files

		if RESIZE_IMAGE:
			if len(files) == 1:
				resized = check_resize_embedded(original_file)
				if resized:
					new_image_file = resized
					files = [original_file, new_image_file]
			else:
				resized = check_resize(image_file)
				if resized:
					filename, extension = path.splitext(image_file)
					new_image_file = f"{filename}-resized{extension}"
					files = [original_file, new_image_file]
		else:
			resized = False

		length = get_length(original_file)
		bitrate = get_bitrate(length)
		print(f"Bitrate: {bitrate}")
		title = get_title(original_file)
		print(f"Title: {title}")
		webm_filepath = path.split(original_file)[0] + path.sep + title + '.webm'

		convert_to_webm(webm_filepath, files, bitrate, title)
		if length >= MAX_LENGTH:
			with open(webm_filepath, 'rb') as file:
				data = bytearray(file.read())
			duration_index = data.find(DURATION_BYTE_MARKER)
			data[duration_index:duration_index+7] = FIVE_MINUTE_DURATION
			with open(webm_filepath, 'wb') as file:
				file.write(data)
		print("Done")

		if resized:
			unlink(new_image_file)
	except Exception:
		if USE_COLOURS:
			colorama.deinit()
		print_exc()
		input()
	else:
		sleep(0.8)
		die()