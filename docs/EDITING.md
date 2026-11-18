# Editing guide

## The layered canvas

- Press **V** for **Select / Move**. Click or drag an unlocked layer, **Shift**-click to select multiple layers, or use the Layers panel. Arrow keys nudge by 1 mm and **Shift** plus an arrow nudges by 5 mm. Hold **Alt** while dragging to bypass snapping.
- Double-click a text layer to edit it. Use **Ctrl+B** or **Ctrl+I** to style selected text and **Ctrl+Enter** to apply. For an arrow that follows two layers, choose **More tools → Linked connector** and drag from one to the other.
- Use the Inspector for exact dimensions and rotation. The Arrange menu has grouping, order, alignment, and distribution controls. The optional guides are visible during editing and omitted in exports.
- Press **M** for **Image Region** and drag a rectangle on a placed PNG. **Cut Out** splits the region into a movable layer and keeps the surrounding pieces. **Copy Region** creates a movable copy. Use **Edit Pixels** for freehand or color selections.

## Pixel selections and placement

Open **Edit Pixels** on a placed PNG. In the **Selection** tab, select a rectangle, draw a freehand outline, or select matching colors. Use **Add** and **Subtract** to combine regions. To select a subject against a mostly uniform background, select the background by color and click **Invert**. Feathering softens edges. **Preview cutout** shows the result over a checkerboard.

| Action | Result |
| --- | --- |
| **Copy to New Layer** | Creates a transparent layer with the selected pixels above the source layer. |
| **Cut to New Layer** | Creates the same layer and leaves a transparent hole in the source. |
| **Move Selection** | Moves selected pixels within the image and leaves transparency at their original position. Click **Apply to Layer** to save the change. |
| **Copy Pixels**, then **Paste into Layer** | Copies pixels within the app and shows a floating preview in the destination image. Open that image in **Edit Pixels** or use **Edit → Paste Pixels into Selected Layer**. |

While a paste is floating, drag it or use the arrow keys to position it. Drag a corner to resize it and use the round handle to rotate it. Hold **Shift** while rotating to snap to 15° increments. Use **Lock ratio** for proportional resizing or type exact dimensions and rotation. Adjust opacity, flip either axis, or use **Reset Transform** to restore the original size and orientation and center the paste. **Place Selection** adds it to the destination bitmap. **Apply to Layer** commits the retouch session to project history.

## Retouch and adjust

- **Retouch** has the brush, eraser, and clone stamp. **Alt**-click with the brush to sample a color. **Alt**-click or right-click with the clone stamp to sample the source pixels. An active selection limits where these edits apply.
- **Adjust** controls black and white points, midpoint gamma, saturation, and individual color channel levels. It also rotates or flips the image.
- **Filters** includes Gaussian blur, unsharp mask, and color inversion. Use **Show before**, **Undo**, and **Redo** to compare changes. Scroll to zoom and middle-drag to pan.

The pixel workspace works on the placed crop in 8-bit RGBA and accepts up to 20 megapixels per layer. A paste is clipped to the destination image bounds. Use a text layer for lettering you want to revise later. When you export, use PNG or SVG for transparent page backgrounds and keep DejaVu Sans installed in external SVG editors for matching font metrics.
