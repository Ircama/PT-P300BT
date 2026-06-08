import sys
import os
import re
import argparse
import serial
from serial.tools import list_ports
from PIL import Image, ImageDraw, ImageFont, ImageOps
from pdf2image import convert_from_path

from labelmaker import do_print_job, reset_printer
    

def set_args():
    """
    Similar to parse_args() in labelmaker, with the addition of
    two other parameters and some change in the help.
    """
    p = argparse.ArgumentParser()
    p.add_argument(
        'comport',
        metavar='COM_PORT',
        help='Printer COM port.'
    )
    p.add_argument(  
        '--fixed-width',  
        type=int,  
        default=None,  
        metavar='MILLIMETERS',  
        help='Pad label to exact width in mm (adds whitespace if text is shorter).'  
    )
    p.add_argument(  
        '--fixed-font-size',  
        type=int,  
        metavar='SIZE',  
        help='Use fixed font size (disables auto-sizing to fit printable area)'  
    )
    p.add_argument(
        'fontname',
        metavar='FONT_NAME',
        nargs='?', default='arial.ttf',
        help='Pathname of the used TrueType or OpenType font.'
    )
    p.add_argument(
        'text_to_print',
        metavar='TEXT_TO_PRINT',
        nargs='*',
        help='Text to be printed. UTF8 characters are accepted. Use \\n for line breaks.'
    )
    p.add_argument(
        '-u', '--unicode',
        help='Use Unicode escape sequences in TEXT_TO_PRINT.',
        action='store_true'
    )
    p.add_argument(
        '-l', '--lines',
        help='Add horizontal lines for drawing area (dotted red) and tape (cyan).',
        action='store_true'
    )
    p.add_argument(
        '-s', '--show',
        help='Show the created image. (If also using -n, terminate.)',
        action='store_true'
    )
    p.add_argument(
        '-c', '--show-conv',
        help='Show the converted image. (If also using -n, terminate.)',
        action='store_true'
    )
    p.add_argument(
        '-i', '--image',
        metavar='FILE_NAME',
        help='Image file to print. If this option is used (legacy mode), TEXT_TO_PRINT and FONT_NAME are ignored.'
    )
    p.add_argument(
        '-M', '--merge',
        metavar='FILE_NAME',
        action='append',
        help='Merge the image file before the text. Can be used multiple times.'
    )
    p.add_argument(
        '-R', '--resize',
        type=float,
        metavar='FLOAT',
        help='With image merge, additionaly resize it (floating point number).',
        default = 1.0
    )
    p.add_argument(
        '-X', '--x-merge',
        type=int,
        metavar='DOTS',
        help='With image merge, shift right the image of X dots.',
        default = 0
    )
    p.add_argument(
        '-Y', '--y-merge',
        metavar='DOTS',
        type=int,
        help='With image merge, shift down the image of Y dots.',
        default = 12
    )
    p.add_argument(
        '--merge-gap',
        metavar='DOTS',
        type=int,
        help='Horizontal gap (in dots) between a merged image and the text.',
        default = 0
    )
    p.add_argument(
        '-S', '--save',
        metavar='FILE_NAME',
        help='Save the produced image to a PNG file.'
    )
    p.add_argument(
        '-n', '--no-print',
        help='Only configure the printer and send the image but do not send print command.',
        action='store_true'
    )
    p.add_argument(
        '-F', '--no-feed',
        help='Disable feeding at the end of the print (chaining).',
        action='store_true'
    )
    p.add_argument(
        '-a', '--auto-cut',
        help='Enable auto-cutting (or print label boundary on e.g. PT-P300BT).',
        action='store_true'
    )
    p.add_argument(
        '-m', '--end-margin',
        metavar='DOTS',
        help='End margin (in dots).',
        default=0,
        type=int
    )
    p.add_argument(
        '-r', '--raw',
        help='Send the image to printer as-is without any pre-processing.',
        action='store_true'
    )
    p.add_argument(
        '-C', '--nocomp',
        help='Disable compression.',
        action='store_true'
    )
    p.add_argument(
        '--fill-color',
        dest="fill",
        help='Fill color for the text (e.g., "white"; default = "black").',
        default="black",
    )
    p.add_argument(
        '--stroke-fill',
        help='Stroke Fill color for the text (e.g., "black"; default = None).',
        default=None,
    )
    p.add_argument(
        '--stroke-width',
        help='Width of the text stroke (e.g., 1 or 2).',
        type=int,
        default=0,
    )
    p.add_argument(
        '--text-size',
        help='Horizontally stretch the text to fit the specified size.',
        metavar='MILLIMETERS',
        type=int,
        default=None,
    )
    p.add_argument(
        '--font-scale',
        type=float,
        default=None,
        metavar='NUMBER',
        help='Scale font size by specified percentage (default: 100%%)'
    )
    p.add_argument(
        '--h-padding',
        type=int,
        default=5,
        metavar='DOTS',
        help='Define custom left and right horizontal padding in pixels'
        ' (default: 5 pixels left and 5 pixels right)'
    )
    p.add_argument(
        '--v-shift',
        type=int,
        default=0,
        metavar='DOTS',
        help='Define relative vertical traslation in pixels'
        ' (default is to vertically center the font)'
    )
    p.add_argument(
        '-p',
        '--line-spacing',
        type=float,
        default=1.2,
        metavar='MULTIPLIER',
        help='Line spacing multiplier for multi-line text (default: 1.2)'
    )
    p.add_argument(
        '-H',
        '--center-text',
        help='Horizontally center text inside the label image.',
        action='store_true'
    )
    p.add_argument(
        '--white-level',
        help='Minimum pixel value to consider it "white" when'
        ' cropping the image. Set it to a value close to 255. (Default: 240)',
        metavar='NUMBER',
        type=int,
        default=240,
    )
    p.add_argument(
        '--threshold',
        help='Custom thresholding when converting the image to binary, to'
        ' manually decide which pixel values become black or white'
        ' (Default: 75)',
        metavar='NUMBER',
        type=int,
        default=75,
    )
    return p


def process_image(image_path, resize, white_level, target_height):
    # Determines if the image is a PDF and converts it to PNG if necessary
    if image_path.lower().endswith('.pdf'):
        image_path = convert_pdf(image_path)  # Sends image_path to convert_pdf() and returns the output_filename

    # Open the image
    img = Image.open(image_path)
    
    # Convert the image to RGBA to ensure it has an alpha channel
    img = img.convert("RGBA")
    pixels = img.load()

    # Create a new white background image with the same size as the original
    white_background = Image.new("RGBA", img.size, (255, 255, 255, 255))
    
    # Paste the original image onto the white background
    white_background.paste(img, (0, 0), img)
    
    # Now 'white_background' has no transparency (transparency is replaced by white)
    img = white_background
    
    # Convert the image to grayscale
    img = img.convert("L")  # "L" mode is for grayscale images
    
    # Get image dimensions
    width, height = img.size

    # Initialize the bounding box coordinates
    left, top, right, bottom = width, height, 0, 0
    
    # Iterate over each pixel to find the bounding box of non-white pixels
    for y in range(height):
        for x in range(width):
            pixel = img.getpixel((x, y))
            
            # White pixels in grayscale have a value close to 255
            if pixel < white_level:  # Consider pixels that are not white
                left = min(left, x)
                right = max(right, x)
                top = min(top, y)
                bottom = max(bottom, y)
    
    # Crop the image to the bounding box
    if right > left and bottom > top:
        cropped_img = img.crop((left, top, right + 1, bottom + 1))
        
        # Get the size of the cropped image
        cropped_width, cropped_height = cropped_img.size
        
        # Calculate the new width to maintain the aspect ratio with target height
        aspect_ratio = cropped_width / cropped_height
        new_width = int(target_height * aspect_ratio)
        
        # Resize the image to target height while maintaining aspect ratio
        return cropped_img.resize(
            (int(new_width * resize), int(target_height * resize)),
            Image.Resampling.LANCZOS
        )
    else:
        print("No content detected to crop.")
    return None


def convert_pdf(filename):
    # Converts the first page of a PDF to a PNG, returns PNG
    output_filename = filename.replace('.pdf', '.png')
    images = convert_from_path(filename, dpi=300, first_page=1, last_page=1) # used defaults, 300dpi may even be overkill for labels
    images[0].save(output_filename, "PNG")
    return output_filename


def calculate_multiline_dimensions(lines, font, line_spacing):
    """Calculate the total width and height needed for multiline text"""
    max_width = 0
    line_heights = []
    # Use the same sample as draw_multiline_text for line height
    sample_bbox = font.getbbox("".join(lines), anchor="lt")
    base_line_height = sample_bbox[3] - sample_bbox[1]
    line_spacing_pixels = base_line_height * line_spacing
    n_lines = len(lines)
    for line in lines:
        bbox = font.getbbox(line, anchor="lt")
        line_width = bbox[2] - bbox[0]
        max_width = max(max_width, line_width)
        line_heights.append(base_line_height)
    total_height = 0
    for i in range(n_lines):
        total_height += base_line_height
        if n_lines > 1 and i < n_lines - 1:
            total_height += (line_spacing_pixels - base_line_height)
    return max_width, int(round(total_height)), line_heights


def draw_multiline_text(
    draw, text_lines, x, y, font, fill,
    stroke_width, stroke_fill, line_spacing, center_text, image_width
):
    """Draw multiple lines of text with proper spacing"""
    if not text_lines:
        return
    
    sample_bbox = font.getbbox("".join(text_lines), anchor="lt")
    base_line_height = sample_bbox[3]
    line_spacing_pixels = base_line_height * line_spacing
    
    current_y = y
    for line in text_lines:
        if line.strip():
            if center_text:
                _, _, line_width, _ = draw.textbbox(
                    (0, 0), line, font=font, stroke_width=stroke_width
                )
                x_pos = (image_width - line_width) // 2
            else:
                x_pos = x
            draw.text(
                (x_pos, current_y), line,
                font=font,
                fill=fill,
                anchor="lt",
                stroke_width=stroke_width,
                stroke_fill=stroke_fill
            )
        current_y += line_spacing_pixels



def main():
    p = set_args()
    args = p.parse_args()
    if not args.comport.startswith("bt:") and \
            args.comport not in [p.device for p in list_ports.comports()]:
        print("Port '" + args.comport + "' does not seem a valid serial communication port.")
    # Legacy -i/--image: print the given image as the whole label, ignoring text
    # and font. The image-building code below only ever ran in the non-legacy
    # branch, so -i used to leave `data` unset and crash; route it through the
    # (working) merge path with empty text instead.
    if args.image is not None:
        args.merge = [args.image] + (args.merge or [])
        args.text_to_print = []
        args.image = None
    data = None
    if args.image is None: # not using the legacy mode
        height_of_the_printable_area = 64  # px: number of vertical pixels of the PT-P300BT printer (9 mm)
        height_of_the_tape = 86  # 64 px / 9 mm * 12 mm (the borders over the printable area will not be printed)
        height_of_the_image = 88  # px (can be any value >= height_of_the_tape, but height_of_the_tape + 2 border lines is good)

        # Compute max TT font size to remain within height_of_the_printable_area
        font_size = 0
        font_height = 0
        font = None
        print_border = (height_of_the_image - height_of_the_printable_area) / 2
        text = " ".join(args.text_to_print)
        if text:
            if args.unicode:
                text = text.encode().decode('unicode_escape')
            
            # Check if text contains newlines to determine processing mode
            has_newlines = '\\n' in text

            if has_newlines:
                # Split text into lines for multiline processing
                text_lines = text.replace("\\n", "\n").split('\n')

                if args.fixed_font_size:
                    font_size = args.fixed_font_size  
                    font = ImageFont.truetype(args.fontname, font_size, encoding='utf-8')  
                    font_width, font_height, line_heights = calculate_multiline_dimensions(  
                        text_lines, font, args.line_spacing  
                    )  
                    if font_height > height_of_the_printable_area:  
                        print(f"Warning: fixed font size {font_size} exceeds printable area ({font_height} > {height_of_the_printable_area})")  
                else:
                    stop = False
                    while font_height != height_of_the_printable_area:
                        if font_height > height_of_the_printable_area:
                            # Try to slightly decrease line_spacing
                            if len(text_lines) > 1:
                                min_spacing = args.line_spacing * 0.9  # Don't go below this multiplier
                                spacing_step = 0.01
                                new_spacing = args.line_spacing
                                found_fit = False
                                while new_spacing > min_spacing:
                                    new_spacing -= spacing_step
                                    font_width2, font_height2, _ = calculate_multiline_dimensions(
                                        text_lines, font, new_spacing
                                    )
                                    if font_height2 == height_of_the_printable_area:
                                        # Found a fit, update spacing and dimensions
                                        args.line_spacing = new_spacing
                                        print(
                                            f"Line spacing has been slightly decreased "
                                            f"to fit the printable area. Used value: {new_spacing:.2f}."
                                        )
                                        font_width, font_height = font_width2, font_height2
                                        found_fit = True
                                        break
                                if found_fit:
                                    break
                            # If not multiline or can't fit by spacing, decrease font size
                            font_size -= 1
                            stop = True
                        else:
                            font_size += 1
                        try:
                            font = ImageFont.truetype(
                                args.fontname, font_size, encoding='utf-8'
                            )
                        except Exception as e:
                            p.error(f'Cannot load font "{args.fontname}" - {e}')
                        
                        # Calculate dimensions for multiline text
                        font_width, font_height, line_heights = calculate_multiline_dimensions(
                            text_lines, font, args.line_spacing
                        )
                        
                        if stop:
                            print(
                                "The max height of this text with font "
                                f'"{args.fontname}" is {font_height} dots'
                                f' instead of {height_of_the_printable_area}.')
                            break

                y_position = print_border
                if args.font_scale:
                    scaled_font_size = int(
                        round(font_size * (args.font_scale / 100.0))
                    )
                    try:
                        font = ImageFont.truetype(
                            args.fontname, scaled_font_size, encoding='utf-8'
                        )
                    except Exception as e:
                        p.error(f'Cannot load font "{args.fontname}" - {e}')
                    
                    # Recalculate dimensions with scaled font
                    font_width, font_height, line_heights = calculate_multiline_dimensions(
                        text_lines, font, args.line_spacing
                    )

                    # Vertically center text
                    y_position = print_border + (
                        height_of_the_printable_area - font_height
                    ) // 2

                # Create a drawing context for the image
                image = Image.new(
                    "RGB",
                    (
                        font_width + args.h_padding * 2 + 1 + args.end_margin,
                        height_of_the_image
                    ),
                    "white"
                )
                draw = ImageDraw.Draw(image)
                try:
                    # Draw multiline text
                    draw_multiline_text(
                        draw, text_lines, args.h_padding, y_position + args.v_shift,
                        font, args.fill, args.stroke_width, args.stroke_fill, args.line_spacing,
                        args.center_text, image.width
                    )
                except Exception as e:
                    p.error(f"Invalid parameter: {e}")
                
                if args.text_size:
                    text_size = (
                        int(args.text_size / 0.149)
                        - args.h_padding
                        - args.end_margin
                    )  # mm to dot
                    
                    # For multiline text, use the width of the widest line for scaling
                    scale_factor = font_width / text_size
                    image = image.transform(
                        (text_size + args.end_margin + args.h_padding, height_of_the_image),
                        Image.Transform.AFFINE,
                        (scale_factor, 0, 0, 0, 1, 0),
                    )
                    while image.getpixel((image.width - 1, 0)) == (0, 0, 0):
                        crop_box = (0, 0, image.width - 1, height_of_the_image)
                        image = image.crop(crop_box)
                    draw = ImageDraw.Draw(image)
            else:
                # Single-line processing
                if args.fixed_font_size:  
                    font_size = args.fixed_font_size  
                    font = ImageFont.truetype(args.fontname, font_size, encoding='utf-8')  
                    font_width, font_height = font.getbbox(text, anchor="lt")[2:]  
                    if font_height > height_of_the_printable_area:  
                        print(f"Warning: fixed font size {font_size} exceeds printable area ({font_height} > {height_of_the_printable_area})")  
                else:
                    stop = False
                    while font_height != height_of_the_printable_area:
                        if font_height > height_of_the_printable_area:
                            font_size -= 1
                            stop = True
                        else:
                            font_size += 1
                        try:
                            font = ImageFont.truetype(
                                args.fontname, font_size, encoding='utf-8'
                            )
                        except Exception as e:
                            p.error(f'Cannot load font "{args.fontname}" - {e}')
                        font_width, font_height = font.getbbox(text, anchor="lt")[2:]
                        if stop:
                            print(
                                "The max height of this text with font "
                                f'"{args.fontname}" is {font_height} dots'
                                f' instead of {height_of_the_printable_area}.')
                            break

                y_position = print_border
                if args.font_scale:
                    scaled_font_size = int(
                        round(font_size * (args.font_scale / 100.0))
                    )
                    try:
                        font = ImageFont.truetype(
                            args.fontname, scaled_font_size, encoding='utf-8'
                        )
                    except Exception as e:
                        p.error(f'Cannot load font "{args.fontname}" - {e}')
                    font_width, font_height = font.getbbox(text, anchor="lt")[2:]

                    # Vertically center text
                    y_position = print_border + (
                        height_of_the_printable_area - font_height
                    ) // 2

                # Create a drawing context for the image
                image = Image.new(
                    "RGB",
                    (
                        font_width + args.h_padding * 2 + 1 + args.end_margin,
                        height_of_the_image
                    ),
                    "white"
                )
                draw = ImageDraw.Draw(image)
                try:
                    draw.text(
                        (args.h_padding, y_position + args.v_shift), text,
                        font=font,
                        fill=args.fill,
                        anchor="lt",
                        stroke_width=args.stroke_width,
                        stroke_fill=args.stroke_fill
                    )
                except Exception as e:
                    p.error(f"Invalid parameter: {e}")
                if args.text_size:
                    text_size = (
                        int(args.text_size / 0.149)
                        - args.h_padding
                        - args.end_margin
                    )  # mm to dot
                    _, _, text_width, text_height = draw.textbbox(
                        (0, 0), text,
                        anchor="lt",
                        font=font,
                        stroke_width=args.stroke_width,
                    )
                    scale_factor = text_width / text_size
                    image = image.transform(
                        (text_size + args.end_margin + args.h_padding, height_of_the_image),
                        Image.Transform.AFFINE,
                        (scale_factor, 0, 0, 0, 1, 0),
                    )
                    while image.getpixel((image.width - 1, 0)) == (0, 0, 0):
                        crop_box = (0, 0, image.width - 1, height_of_the_image)
                        image = image.crop(crop_box)
                    draw = ImageDraw.Draw(image)
        else:  # null image
            image = Image.new(
                "RGB",
                (0, height_of_the_image),
                "white"
            )
            draw = ImageDraw.Draw(image)

        if not args.fixed_font_size:  
            print("Font size determined:", font_size)
        if args.merge:
            for i in reversed(args.merge):
                loaded_image = process_image(
                    i,
                    args.resize,
                    white_level=args.white_level,
                    target_height=height_of_the_printable_area
                )
                if not loaded_image:
                    p.error(f'Invalid image "{i}"')
                gap = args.merge_gap if image.width else 0
                dst = Image.new(
                    "RGB",
                    (loaded_image.width + gap + image.width, height_of_the_image),
                    "white"
                )
                dst.paste(loaded_image, (args.x_merge, args.y_merge))
                dst.paste(image, (loaded_image.width + gap, 0))
                image = dst
            # Convert the image to binary
            draw = ImageDraw.Draw(image)

        if args.fixed_width:  
            target_width_dots = int(round(args.fixed_width / 0.149))  
            current_width = image.width  
            if current_width < target_width_dots:  
                # Create a new white image of target width and paste the existing image centered or left-aligned  
                padded_image = Image.new("RGB", (target_width_dots, image.height), "white")  
                # Example: left-aligned paste; change x_offset for centering  
                x_offset = 0  
                padded_image.paste(image, (x_offset, 0))  
                image = padded_image

        if args.lines:
            # Draw ruler (in)
            draw.text(
                (0, 1), "in",
                anchor="la",
                fill="magenta"
            )
            x = -1
            i = 0
            while x < image.width:
                if x > 0:
                    draw.line(  # top
                        (
                            int(x), print_border - (4 if i % 4 else 9),
                            int(x), print_border - 2
                        ),
                        fill="magenta", width=2
                    )
                x += 43.18
                i += 1
            # Draw ruler (cm)
            draw.text(
                (0, 76), "cm",
                anchor="la",
                fill="magenta"
            )
            x = -1
            i = 0
            while x < image.width:
                if x > 0:
                    draw.line(
                        (
                            int(x), height_of_the_image - print_border + 1,
                            int(x), height_of_the_image - print_border
                            + (5 if i % 10 else 9)
                        ),
                        fill="magenta", width=2
                    )
                x += 68
                i += 1
            # Draw a dotted horizontal line over the top border and below the bottom border of the printable area
            for x in range(0, image.width, 5):
                draw.line(  # top
                    (x, print_border - 1, x + 1, print_border - 1),
                    fill="red", width=1
                )
                draw.line(
                    (  # bottom
                        x, height_of_the_image - print_border,
                        x + 1, height_of_the_image - print_border
                    ),
                    fill="red", width=1
                )
            # Draw a cyan line showing the tape borders
            tape_border = int((height_of_the_image - height_of_the_tape) / 2)
            if tape_border > 0:
                draw.line(
                    (0, tape_border - 1, image.width, tape_border - 1),
                    fill="cyan", width=1
                )
                draw.line(
                    (
                        0, height_of_the_image - tape_border,
                        image.width, height_of_the_image - tape_border
                    ),
                    fill="cyan", width=1
                )

        # Convert to greyscale and rotate/invert/mirror the image
        rotated_image = ImageOps.invert(
            image.convert('L', dither=Image.Dither.FLOYDSTEINBERG)
            .rotate(-90, expand=True, resample=Image.BICUBIC)
        )
        rotated_image = ImageOps.mirror(rotated_image)

        # Manual binarization with a threshold (smoother control of artifacts)
        bin_image = rotated_image.point(lambda p: p > args.threshold and 255)

        # Convert to '1' mode (binary image)
        binary_img = bin_image.convert('1')

        # Add padding to increase the height from height_of_the_image to 128
        # (similar to the last part of read_png() code in labelmaker_encode.py)
        w, h = binary_img.size
        padded = Image.new('1', (128, h))
        x, y = (128 - w) // 2, 0
        nw, nh = x + w, y + h
        padded.paste(binary_img, (x, y, nw, nh))
        
        if not image.tobytes():
            p.error("Null image generated.")

        # Compute tape length and print duration
        print_length = padded.size[1] * 0.149  # mm
        print(
            "Length of the printed tape:",
            "%.1f" % (print_length / 10),
            "cm = %.1f" % (print_length / 10 / 2.54),
            "in, printed in",
            "%.1f" % (print_length / 20),
            "sec."
        )
        print_length += (25 + 1)  # 2.5 cm of wasted tape before, 1 mm after
        print(
            "Length of the used tape (adding header and footer):",
            "%.1f" % (print_length / 10),
            "cm = %.1f" % (print_length / 10 / 2.54),
            "in, printed in",
            "%.1f" % (print_length / 20),
            "sec."
        )

        # Check max tape length
        if print_length > 499:
            print("Print length exceeding 49.9 cm = 19.6 in")
            sys.exit()

        # Image save and show
        if args.save:
            print(f'Saving image "{args.save}".')
            image.save(args.save)
            if args.no_print:
                sys.exit()
        if args.show:
            try:
                image.show()
            except Exception as e:
                p.error("Cannot show image:" + repr(e))
            if not args.show_conv and args.no_print:
                sys.exit()
        if args.show_conv:
            padded.show()
            if args.no_print:
                sys.exit()

        data = padded.tobytes()

    # Similar to main() in labelmaker.py
    try:
        if args.comport.startswith("bt:"):
            # Native macOS IOBluetooth RFCOMM transport (pure Python via PyObjC).
            # The /dev/cu.* Bluetooth-serial bridge is unreliable for this printer
            # (pyserial's close() doesn't drain the macOS run loop, so the RFCOMM
            # channel is left half-open and the next open() hangs). "bt:NAME"
            # matches the paired device whose name contains NAME (default PT-P300).
            sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "native"))
            from btnative import BTSerial
            name = args.comport[3:] or "PT-P300"
            ser = BTSerial(name=name, timeout=3)
        else:
            ser = serial.Serial(args.comport, timeout=3)
    except serial.SerialException:
        p.error(
            'Printer on Bluetooth serial port "'
            + args.comport
            + '" is unavailable or unreachable.'
        )
    except Exception as e:
        p.error(e)

    try:
        assert data is not None
        do_print_job(ser, args, data)
    finally:
        # Initialize
        reset_printer(ser)

if __name__ == "__main__":
    main()
