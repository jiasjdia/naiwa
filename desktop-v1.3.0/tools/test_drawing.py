"""Shape geometry and real desktop preview/clone placement checks."""
import math
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
RUN_GUI = "--gui" in sys.argv
# Import before stripping --gui: the shared fixture owns temporary settings/audio.
from tools import test_group as fixtures
from pet_drawing import PetDrawing, shape_points
if "--gui" in sys.argv:
    sys.argv.remove("--gui")


class ShapeTests(unittest.TestCase):
    def test_point_and_degenerate_shapes_make_one_pet(self):
        for shape in ("point", "line", "rectangle", "circle"):
            self.assertEqual(shape_points(shape, (20, 30), (20, 30), 50), [(20, 30)])

    def test_line_has_both_endpoints_and_uniform_spacing(self):
        self.assertEqual(shape_points("line", (0, 0), (300, 0), 100),
                         [(0, 0), (100, 0), (200, 0), (300, 0)])
        self.assertEqual(len(shape_points("line", (0, 0), (300, 0), 50)), 7)

    def test_rectangle_corners_are_unique_and_only_outline_is_filled(self):
        points = shape_points("rectangle", (0, 0), (200, 100), 100)
        self.assertEqual(len(points), 6)
        self.assertEqual(len(set(points)), 6)
        self.assertTrue(all(x in (0, 200) or y in (0, 100) for x, y in points))
        self.assertTrue({(0, 0), (200, 0), (200, 100), (0, 100)} <= set(points))

    def test_circle_uses_start_as_center_and_drag_distance_as_radius(self):
        points = shape_points("circle", (120, 130), (220, 130), 40)
        for point in points:
            self.assertAlmostEqual(math.dist((120, 130), point), 100)
        distances = [math.dist(a, b) for a, b in zip(points, points[1:]+points[:1])]
        self.assertAlmostEqual(max(distances), min(distances))


@unittest.skipUnless(RUN_GUI, "Use --gui for desktop preview and real clones")
class DrawingWindows(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.TkGroupTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.group, self.app = self.fixture.group, self.fixture.app

    def test_preview_commits_selected_size_positions_and_preserves_primary(self):
        for shape in ("point", "line", "rectangle", "circle"):
            with self.subTest(shape=shape):
                self.group.draw_clones(self.app)
                drawing = self.group.drawing
                drawing.scale.set(0.4)
                drawing.shape.set(shape)
                drawing.begin()
                self.assertTrue(drawing.overlay.winfo_exists())
                start, end = SimpleNamespace(x=250, y=250), SimpleNamespace(x=550, y=450)
                drawing.press(start)
                drawing.motion(end)
                expected = drawing.points((end.x, end.y))
                before = set(self.group.pets)
                drawing.release(end)
                self.assertEqual(set(self.group.pets), before)
                drawing.confirm()
                self.fixture.pump(1.5)
                created = [p for p in self.group.pets if p not in before]
                self.assertEqual(len(created), len(expected))
                self.assertIs(self.group.primary, self.app)
                self.assertIsNone(self.group.drawing)
                for pet, point in zip(created, expected):
                    self.assertEqual(pet.global_scale, 0.4)
                    box = pet.visible_box()
                    center = (pet.root.winfo_x()+(box[0]+box[2])/2,
                              pet.root.winfo_y()+(box[1]+box[3])/2)
                    self.assertAlmostEqual(center[0], point[0]+drawing.area[0], delta=1)
                    self.assertAlmostEqual(center[1], point[1]+drawing.area[1], delta=1)
                for pet in created:
                    self.group.destroy_pet(pet)

    def test_cancel_preview_and_queued_creation_release_windows_and_callbacks(self):
        self.group.draw_clones(self.app)
        drawing = self.group.drawing
        drawing.begin()
        drawing.close()
        self.assertIsNone(self.group.drawing)
        self.assertEqual(len(self.group.pets), 1)
        self.group.draw_clones(self.app)
        drawing = self.group.drawing
        drawing.shape.set("line")
        drawing.begin()
        drawing.press(SimpleNamespace(x=100, y=200))
        drawing.release(SimpleNamespace(x=1200, y=200))
        drawing.confirm()
        count = len(self.group.pets)
        drawing.close()
        self.fixture.pump(0.1)
        self.assertEqual(len(self.group.pets), count)
        self.assertIsNone(drawing.after)

    def test_released_shape_can_move_without_spawning_then_redraw_or_confirm(self):
        self.group.draw_clones(self.app)
        drawing = self.group.drawing
        drawing.shape.set("line")
        drawing.scale.set(0.4)
        drawing.begin()
        drawing.press(SimpleNamespace(x=250, y=250))
        drawing.release(SimpleNamespace(x=550, y=250))
        original = list(drawing.placed_points)
        drawing.press(SimpleNamespace(x=300, y=250))
        drawing.motion(SimpleNamespace(x=400, y=330))
        drawing.release(SimpleNamespace(x=400, y=330))
        self.assertEqual(drawing.placed_points, [(x+100, y+80) for x, y in original])
        self.assertEqual(len(self.group.pets), 1)
        drawing.redraw()
        self.assertFalse(drawing.adjusting)
        self.assertEqual(drawing.placed_points, [])
        drawing.press(SimpleNamespace(x=300, y=350))
        drawing.release(SimpleNamespace(x=600, y=350))
        expected = list(drawing.placed_points)
        drawing.confirm()
        self.fixture.pump(0.8)
        self.assertEqual(len(self.group.pets), 1+len(expected))


if __name__ == "__main__":
    unittest.main()
