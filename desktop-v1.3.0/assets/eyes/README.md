# Eye sprites

Generated with the built-in imagegen tool using `大笑/frame_00001.png` as the visual reference. The source animation frames are not replaced. `geometry.json` calibrates the eye mask and sprite position in the 320x305 idle frame.

## Backing prompt

Create ONE isolated eye-background sprite for the visible large green eye of this exact yellow desktop-pet character. The input image is a style and geometry reference. Output only a close-up of the green iris/eyeball, centered, taking about 80 percent of a square transparent canvas. Match its original slightly vertically oval, obliquely viewed shape, smooth green material, soft upper-left illumination, darker lower rim and subtle yellow-green thin rim. Remove the black pupil completely and reconstruct continuous green shading under it. No black center, no dark spot where a pupil was, no white sclera, no yellow face, no eyelids outside the thin rim, no text, no shadow outside the eye. This is the static green backing underneath a separately movable pupil. Preserve the toy-like softly shaded 3D appearance of the reference.

## Pupil prompt

Create ONE isolated black pupil sprite, matching the pupil in the large green eye of this exact yellow character reference. Output only the pupil, centered on a square transparent canvas with generous transparent margins. The pupil is a soft-edged, near-black vertically oval disk, width about 75 percent of height, slightly tilted like the reference. It has a very subtle small softly blurred upper-left reflected glint, not a big white cartoon dot. Smooth shaded 3D toy material. No green iris, no eye rim, no white sclera, no face, no shadow outside the pupil, no text. Pupil takes about 70 percent of the image height. This sprite will be moved a tiny distance on top of the character's existing green eye.

`backing.png` and `pupil.png` are the original generated outputs. Runtime code crops their transparent margins, sizes them to the calibrated eye, and clips the result with a fixed mask so the original eye rim stays in place.
