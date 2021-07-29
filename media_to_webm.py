from contextlib import suppress
from math import floor
from mutagen import File
from os import unlink, path
from subprocess import run
from sys import argv
from time import sleep
from traceback import print_exc

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

def warning(text):
	print(YELLOW+text+WHITE)

def error(text):
	print(RED+text+WHITE)

def get_length(file):
	try:
		file_info = File(file)
		return file_info.info.length
	except AttributeError:
		while True:
			l = input("Length (in seconds): ")
			with suppress(ValueError):
				return int(l)

def get_bitrate(length):
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

def handle_large_webm(webm, files, bitrate):
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
			convert_to_webm(webm, files, new_bitrate)

def convert_to_webm(webm, files, bitrate):
	command = FFMPEG_PATH
	command += ''.join(f' -i "{filepath}"' for filepath in files)
	command += f' -c:v libvpx -c:a libvorbis -b:a {bitrate}k "{webm}"'
	run(command, shell=True, capture_output=True)
	with open(webm, 'rb') as file:
		data = bytearray(file.read())
	if len(data) > MAX_FILE_SIZE:
		handle_large_webm(webm, files, bitrate)

def webm_info(filepath):
	folder, filename = path.split(filepath)
	webm_filepath = folder + path.sep + path.splitext(filename)[0] + '.webm'
	return webm_filepath, get_length(filepath)

def resize(image, image_file, side_length):
	factor = 1
	score = 1000000
	while True:
		if IMAGE_MIN < (side_length // (factor+1)) < IMAGE_MAX:
			new_score = abs((IMAGE_MAX + IMAGE_MIN)//2 - (side_length // (factor+1)))
			if new_score > score:
				break
			score = new_score
		elif IMAGE_MIN > (side_length // (factor+1)):
			break
		factor += 1
	print(f"Resizing image to {image.width // factor}x{image.height // factor}")
	filename, extension = path.splitext(image_file)
	print(extension)
	new_filepath = f"{filename}-resized{extension}"
	if image_library == 'wand':
		image.resize(image.width // factor, image.height // factor)
		image.save(filename=new_filepath)
	else:
		image.resize((image.width // factor, image.height // factor))
		image.save(new_filepath)

def check_resize(filepath):
	if image_library == 'wand':
		i = Image(filename=filepath)
	else:
		i = Image.open(filepath)
	side_length = max(i.size)
	if side_length <= IMAGE_MAX:
		return False
	resize(i, filepath, side_length)
	return True

def die():
	if USE_COLOURS:
		colorama.deinit()
	quit()

def check_resize_embedded(file):
	audio_file = File(file)
	if {'audio/mp3', 'audio/wav'} & set(audio_file.mime):
		image = audio_file.tags.get('APIC:')
		if image is None:
			while (answer := input('No embedded image found. Continue anyway? [y/n] ').lower()) not in 'yn':
				pass
			if answer == 'y':
				return None
			else:
				die()
		image = image.data
	elif 'audio/flac' in audio_file.mime:
		if not audio_file.pictures:
			while (answer := input('No embedded image found. Continue anyway? [y/n] ').lower()) not in 'yn':
				pass
			if answer == 'y':
				return None
			else:
				die()
		image = audio_file.pictures[0].data
	elif 'audio/aac' in audio_file.mime:
		image = audio_file.tags.get('covr')
		if image is None:
			while (answer := input('No embedded image found. Continue anyway? [y/n] ').lower()) not in 'yn':
				pass
			if answer == 'y':
				return None
			else:
				die()
	else:
		while (answer := input('No embedded image found. Continue anyway? [y/n] ').lower()) not in 'yn':
			pass
		if answer == 'y':
			return None
		else:
			die()
	
	image = Image(blob=image)
	extension = image.mimetype.split('/')[1]
	side_length = max(image.size)
	if side_length > IMAGE_MAX:
		filename = path.splitext(file)[0]
		resize(image, f'{filename}-image.{extension}', side_length)
		return f'{filename}-image-resized.{extension}'
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
					files = (original_file, new_image_file)
			else:
				resized = check_resize(image_file)
				if resized:
					filename, extension = path.splitext(image_file)
					new_image_file = f"{filename}-resized{extension}"
					files = (original_file, new_image_file)
		else:
			resized = False

		webm_filepath, length = webm_info(original_file)
		bitrate = get_bitrate(length)
		print(f"Bitrate: {bitrate}")

		convert_to_webm(webm_filepath, files, bitrate)
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