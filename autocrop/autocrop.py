# -*- coding: utf-8 -*-

from __future__ import print_function, division

import cv2
import numpy as np
import os
import sys
from PIL import Image

from .constants import (
    MINFACE,
    GAMMA_THRES,
    GAMMA,
    CV2_FILETYPES,
    PILLOW_FILETYPES,
    CASCFILE,
    EYES_RATIO,
)

COMBINED_FILETYPES = CV2_FILETYPES + PILLOW_FILETYPES
INPUT_FILETYPES = COMBINED_FILETYPES + [s.upper() for s in COMBINED_FILETYPES]


class ImageReadError(BaseException):
    """Custom exception to catch an OpenCV failure type"""

    pass


def bgr_to_rbg(img):
    """Given a BGR (cv2) numpy array, returns a RBG (standard) array."""
    dimensions = len(img.shape)
    if dimensions == 1:
        return img
    return img[..., ::-1]


def gamma(img, correction):
    """Simple gamma correction to brighten faces"""
    img = cv2.pow(img / 255.0, correction)
    return np.uint8(img * 255)


def check_underexposed(image, gray):
    """Returns the (cropped) image with GAMMA applied if underexposition
    is detected."""
    uexp = cv2.calcHist([gray], [0], None, [256], [0, 256])
    if sum(uexp[-26:]) < GAMMA_THRES * sum(uexp):
        image = gamma(image, GAMMA)
    return image


def check_positive_scalar(num):
    """Returns True if value if a positive scalar"""
    if num > 0 and not isinstance(num, str) and np.isscalar(num):
        return int(num)
    raise ValueError("A positive scalar is required")


def check_valid_pad_dict(dic):
    """Returns dic if valid, else raises ValueError"""
    valid_keys = {
        "pad_top",
        "pad_right",
        "pad_bottom",
        "pad_left",
    }
    error = "Padding arguments must use keys {} and be positive scalars".format(
        valid_keys
    )
    conditions = []
    conditions.append(isinstance(dic, dict))
    conditions.append(len(dic) == 4)
    conditions.append(set(dic.keys()) == valid_keys)
    conditions.append(all(check_positive_scalar(n) for n in dic.values()))
    if not all(conditions):
        raise ValueError(error)
    return dic


def open_file(input_filename):
    """Given a filename, returns a numpy array"""
    extension = os.path.splitext(input_filename)[1].lower()

    if extension in CV2_FILETYPES:
        # Try with cv2
        return cv2.imread(input_filename)
    if extension in PILLOW_FILETYPES:
        # Try with PIL
        with Image.open(input_filename) as img_orig:
            return np.asarray(img_orig)
    return None


class Cropper(object):
    """
    Crops the largest detected face from images.

    This class uses the CascadeClassifier from OpenCV to
    perform the `crop` by taking in either a filepath or
    Numpy array, and returning a Numpy array. By default,
    also provides a slight gamma fix to lighten the face
    in its new context.

    Parameters:
    -----------

    width : int, default=500
        The width of the resulting array.
    height : int, default=500
        The height of the resulting array.
    face_percent: int, default=50
        Aka zoom factor. Percent of the overall size of
        the cropped image containing the detected coordinates.
    padding: int or dict, default=50
        Number of pixels to pad around the largest detected
        face. Overrides `face_percent`. Expects dict
         padding = {
            "pad_top": int,
            "pad_right": int,
            "pad_bottom": int,
            "pad_left": int
            }
    fix_gamma: bool, default=True
        Cropped faces are often underexposed when taken
        out of their context. If under a threshold, sets the
        gamma to 0.9.
    portrait: bool, default=True
        Controls the composition of the cropped image. If
        `height` > `width`, places vertical margins around the
        face according to the rule of thirds. If False, centers
        the middle of the face vertically.

    """

    def __init__(
        self,
        width=500,
        height=500,
        face_percent=50,
        padding=None,
        fix_gamma=True,
        portrait=True,
    ):
        self.height = check_positive_scalar(height)
        self.width = check_positive_scalar(width)
        self.aspect_ratio = width / height
        self.gamma = fix_gamma
        self.portrait = portrait

        # Face percent
        if face_percent > 100:
            fp_error = "The face_percent argument must be between 0 and 100"
            raise ValueError(fp_error)
        self.face_percent = check_positive_scalar(face_percent)

        # Padding
        if isinstance(padding, int):
            pad = check_positive_scalar(padding)
            self.pad_top = pad
            self.pad_right = pad
            self.pad_bottom = pad
            self.pad_left = pad
        else:
            pad = check_valid_pad_dict(padding)
            self.pad_top = pad["pad_top"]
            self.pad_right = pad["pad_right"]
            self.pad_bottom = pad["pad_bottom"]
            self.pad_left = pad["pad_left"]

        # XML Resource
        directory = os.path.dirname(sys.modules["autocrop"].__file__)
        self.casc_path = os.path.join(directory, CASCFILE)

    def crop(self, path_or_array):
        """Given a file path or np.ndarray image with a face,
        returns cropped np.ndarray around the largest detected
        face.

        Parameters
        ----------
        path_or_array : {str, np.ndarray}
            The filepath or numpy array of the image.

        Returns
        -------
        image : {np.ndarray, None}
            A cropped numpy array if face detected, else None.
        """
        if isinstance(path_or_array, str):
            image = open_file(path_or_array)
        else:
            image = path_or_array

        # Some grayscale color profiles can throw errors, catch them
        try:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        except cv2.error:
            gray = image

        # Scale the image
        try:
            img_height, img_width = image.shape[:2]
        except AttributeError:
            raise ImageReadError
        minface = int(np.sqrt(img_height ** 2 + img_width ** 2) / MINFACE)

        # Create the haar cascade
        face_cascade = cv2.CascadeClassifier(self.casc_path)

        # ====== Detect faces in the image ======
        faces = face_cascade.detectMultiScale(
            gray,
            scaleFactor=1.1,
            minNeighbors=5,
            minSize=(minface, minface),
            flags=cv2.CASCADE_FIND_BIGGEST_OBJECT | cv2.CASCADE_DO_ROUGH_SEARCH,
        )

        # Handle no faces
        if len(faces) == 0:
            return None

        # Make padding from biggest face found
        x, y, w, h = faces[-1]
        pos = self._crop_positions(img_height, img_width, x, y, w, h,)
        # ====== Actual cropping ======
        image = image[pos[0] : pos[1], pos[2] : pos[3]]

        # Resize
        image = cv2.resize(
            image, (self.width, self.height), interpolation=cv2.INTER_AREA
        )

        # Underexposition
        if self.gamma:
            image = check_underexposed(image, gray)
        return bgr_to_rbg(image)

    def _apply_padding():
        """Returns the coordinates if padding is set"""
        # TODO
        pass

    def _expand_centered(self, x, w, imgw, expected_width=None):
        """
        Returns coordinates with extra margin centered around the
        detected face. If the calculated coordinates fall outside
        the image, fits the biggest padding possible. Uses x, but
        can be used for y.

        Parameters:
        -----------
        x : int
            Smallest detected face coordinate (px)
        w : int
            Width of the detected face (px)
        imgw : int
            Total width of the image to be cropped (px)
        expected_width : int or None, default=False
            If int, returns centered values

        Returns:
        --------
        h1: int
            Smallest coordinate
        h2: int
            Largest coordinate
        expectations_met : bool
            Whether the coordinates match self.face_percent

        Diagram:
        --------
        i / j = self.face_percent

                 +
       h1        |         h2
       +---------|---------+
       |      MAR|GIN      |
       |         (x+w, y+h)|
       |   +-----|-----+   |
       |   |   FA|CE   |   |
       |   |     |     |   |
       |   ├──i──┤     |   |
       |   |     |     |   |
       |   |     |     |   |
       |   +-----|-----+   |
       |   (x, y)|         |
       |         |         |
       +---------|---------+
       ├────j────┤
                 + center
        """
        i = int(w / 2)
        j = int(i / self.face_percent)
        center = x + i
        h1 = center - j
        h2 = center + j

        # Handle expected_width corner case
        if expected_width is not None:
            half = int(expected_width / 2)
            h1 = center - half
            h2 = center + half
            return h1, h2, True

        # Margins fall outside image
        expectations_met = True
        if h1 < 0:
            expectations_met = False
            h1 = 0
            h2 = 2 * center
        if h2 > imgw:
            expectations_met = False
            h2 = imgw
            h1 = 2 * center - h2
        return h1, h2, expectations_met

    def _expand_portrait(self, y, h, imgh, expected_height):
        """
        Returns padded coordinates composed around the face using
        the rule of thirds at eye level. If the calculated coordinates
        fall outside the image, fits the biggest padding possible.

        Parameters:
        -----------
        y : int
            Bottom-most detected face coordinate (px)
        h : int
            Height of the detected face (px)
        imgh : int
            Total height of the image to be cropped (px)
        expected_height : int
            Based on the desired aspect_ratio, height we expect to
            be getting. If the margin falls outside the image,
            we might need to return shorter coordinates.

        Returns:
        --------
        v1: int
            Lowest vertical coordinate
        v2: int
            Highest vertical coordinate
        expectations_met : bool
            Whether (v2 - v1) = expected_height

        Diagram:
        --------
        i / j = EYES_RATIO

       +------------------+ v2  ╮
       |                  |     |
    1/3|        (x+w, y+h)|     |
       |   +----------+   |     |
       | i |          |   |     |
    +----------eyes----------+  ├ height
       |   |          |   |     |
       | j |          |   |     |
       |   |   FACE   |   |     |
    2/3|   +----------+   |     |
       |   (x, y)         |     |
       |                  |     |
       |      MARGIN      |     |
       +------------------+ v1  ╯
       """
        eye_height = y + int(EYES_RATIO * h)
        third = int(expected_height / 3)
        v1 = eye_height - 2 * third
        v2 = eye_height + third

        # Margins fall outside image
        expectations_met = True
        if v1 < 0:
            expectations_met = False
            v1 = 0
            v2 = int(1.5 * eye_height)
        if v2 > imgh:
            expectations_met = False
            v2 = imgh
            v1 = eye_height - 2 * (v2 - eye_height)
        return v1, v2, expectations_met

    def _crop_positions(
        self, img_height, img_width, x, y, w, h,
    ):
        """Retuns the coordinates of the crop position centered
        around the detected face with extra margins.

        Parameters:
        -----------
        img_height: int
            Height (px) of the image to be cropped
        img_width: int
            Width (px) of the image to be cropped
        x: int
            Leftmost coordinates of the detected face
        y: int
            Bottom-most coordinates of the detected face
        w: int
            Width of the detected face
        h: int
            Height of the detected face
        """
        if padding:
            # TODO
            return self._apply_padding()

        # Portrait Mode
        tall = True if self.width > self.height else False
        if tall and self.portrait:
            use_face_percent = {"w": False, "h": False}  # Whether we use face_percent

            h1, h2, use_face_percent["w"] = self._expand_centered(x, w, imgw)
            if not use_face_percent["w"]:
                expected_height = int((h2 - h1) / self.aspect_ratio)
            else:
                expected_height = int(w / self.aspect_ratio)

            v1, v2, use_face_percent["h"] = self._expand_portrait(
                y, h, imgh, expected_height
            )
            if not use_face_percent["h"]:
                expected_width = int()
                h1, h2, use_face_percent["w"] = self._expand_centered(
                    x, w, imgw, expected_width
                )
            assert all(v for v in use_face_percent.values())
            return [h1, h2, v1, v2]

        # Centered Mode

        # Adjust output height based on percent
        height_crop = h * 100.0 / self.face_percent
        width_crop = aspect_ratio * float(height_crop)

        # Calculate padding by centering face
        xpad = (width_crop - w) / 2
        ypad = (height_crop - h) / 2

        # Calc. positions of crop
        h1 = float(x - (xpad * self.pad_left / (self.pad_left + self.pad_right)))
        h2 = float(x + w + (xpad * self.pad_right / (self.pad_left + self.pad_right)))
        v1 = float(y - (ypad * self.pad_top / (self.pad_top + self.pad_bottom)))
        v2 = float(y + h + (ypad * self.pad_bottom / (self.pad_top + self.pad_bottom)))

        # Determine padding ratios
        left_pad_ratio = self.pad_left / (self.pad_left + self.pad_right)
        right_pad_ratio = self.pad_left / (self.pad_left + self.pad_right)
        top_pad_ratio = self.pad_top / (self.pad_top + self.pad_bottom)
        bottom_pad_ratio = self.pad_bottom / (self.pad_top + self.pad_bottom)

        # Calculate largest bounds with padding ratios
        delta_h = 0.0
        if h1 < 0:
            delta_h = abs(h1) / left_pad_ratio

        if h2 > img_width:
            delta_h = max(delta_h, (h2 - img_width) / right_pad_ratio)

        delta_v = 0.0 if delta_h <= 0.0 else delta_h / aspect_ratio

        if v1 < 0:
            delta_v = max(delta_v, abs(v1) / top_pad_ratio)

        if v2 > img_height:
            delta_v = max(delta_v, (v2 - img_height) / bottom_pad_ratio)

        delta_h = max(delta_h, delta_v * aspect_ratio)

        # Adjust crop values accordingly
        h1 += delta_h * left_pad_ratio
        h2 -= delta_h * right_pad_ratio
        v1 += delta_v * top_pad_ratio
        v2 -= delta_v * bottom_pad_ratio

        return [int(v1), int(v2), int(h1), int(h2)]
