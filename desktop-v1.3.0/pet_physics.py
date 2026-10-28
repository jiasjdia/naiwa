"""Deterministic, Tk-independent physics for emitted desktop pets."""
from __future__ import annotations

from dataclasses import dataclass
import math
import random

import pymunk

TIERS = (1.0, 0.8, 0.6, 0.4)
GROWTH_STEPS = 6
GROWTH_INCREMENT = round(TIERS[0] - TIERS[1], 8) / GROWTH_STEPS
STEP = 1 / 120
MOTION_RATE = 0.75  # Slow the whole trajectory without changing animation/audio clocks.
WALL_RESTITUTION = 0.40625
FLOOR_RESTITUTION = 0.34375


def snap_scale(value):
    # Round the distance so binary floating-point ties prefer the larger size.
    return min(TIERS, key=lambda s: (round(abs(float(value) - s), 9), -s))


def tier_of(value):
    return TIERS.index(snap_scale(value)) + 1


@dataclass
class Particle:
    body: pymunk.Body
    shape: pymunk.Shape
    area: tuple
    offset: tuple
    source: object = None
    speed_multiplier: float = 1


class PhysicsWorld:
    """One independent space per monitor, stepped by one group clock."""
    def __init__(self):
        self.spaces = {}
        self.particles = {}
        self.accumulator = 0.0

    def _space(self, area):
        area = tuple(area)
        if area in self.spaces:
            return self.spaces[area]
        left, top, right, bottom = area
        space = pymunk.Space()
        space.gravity = (0, 5400 * (bottom - top) / 1080)
        space.iterations = 30
        space.damping = 0.985
        space.sleep_time_threshold = 0.6
        space.idle_speed_threshold = 10
        space.collision_slop = 0.3
        for a, b, kind in (((left, top), (right, top), 2),
                           ((left, top), (left, bottom), 2),
                           ((right, top), (right, bottom), 2),
                           ((left, bottom), (right, bottom), 3)):
            wall = pymunk.Segment(space.static_body, a, b, 2)
            wall.collision_type = kind
            wall.friction = 0.8
            space.add(wall)
        space.on_collision(1, 1, pre_solve=self._contact)
        space.on_collision(1, 2, pre_solve=self._contact)
        space.on_collision(1, 3, pre_solve=self._contact)
        self.spaces[area] = space
        return space

    def _contact(self, arbiter, space, data):
        a, b = arbiter.shapes
        pa, pb = getattr(a, "pet_owner", None), getattr(b, "pet_owner", None)
        ea, eb = self.particles.get(pa), self.particles.get(pb)
        if ((ea is not None and ea.source is pb and pb is not None)
                or (eb is not None and eb.source is pa and pa is not None)):
            arbiter.process_collision = False
            return
        kinds = {a.collision_type, b.collision_type}
        arbiter.restitution = FLOOR_RESTITUTION if 3 in kinds else WALL_RESTITUTION if 2 in kinds else 0.30
        # Avoid an endless sequence of subpixel hops at resting contacts.
        relative = a.body.velocity - b.body.velocity
        if relative.length < 45:
            arbiter.restitution = 0
        # Smooth walls and low torso friction prevent a dense burst wedging
        # into a floating arch between both walls and the hovering emitter.
        arbiter.friction = 0.7 if 3 in kinds else 0 if 2 in kinds else 0.15

    def add(self, pet, box, origin, area, *, dynamic=False, velocity=(0, 0), source=None, speed_multiplier=1):
        self.remove(pet)
        area = tuple(area)
        space = self._space(area)
        x0, y0, x1, y1 = box
        width, height = x1 - x0, y1 - y0
        # The idle silhouette is narrower than the laughing torso. A small
        # allowance keeps bellies apart without enveloping swinging hands/feet.
        radius = max(2, min(width * 0.55, height / 2))
        offset = ((x0 + x1) / 2, (y0 + y1) / 2)
        mass = max(0.1, width * height / 10000)
        body = pymunk.Body(mass, float("inf")) if dynamic else pymunk.Body(body_type=pymunk.Body.STATIC)
        body.position = origin[0] + offset[0], origin[1] + offset[1]
        body.velocity = velocity if dynamic else (0, 0)
        half_line = max(0, height / 2 - radius)
        shape = pymunk.Segment(body, (0, -half_line), (0, half_line), radius)
        shape.collision_type = 1
        shape.pet_owner = pet
        entry = Particle(body, shape, area, offset, source, speed_multiplier)
        self.particles[pet] = entry
        space.add(body, shape)
        self._wake(space)
        self._contain(entry)
        return entry

    def _contain(self, entry):
        """Recover missed wall contacts, including throws released outside a screen."""
        body, shape = entry.body, entry.shape
        if body.body_type != pymunk.Body.DYNAMIC:
            return
        left, top, right, bottom = entry.area
        rx, ry = shape.radius, shape.radius + abs(shape.b.y)
        x, y = body.position
        min_x, max_x = left + rx, right - rx
        min_y, max_y = top + ry, bottom - ry
        nx = min(max(x, min_x), max_x) if min_x <= max_x else (left + right) / 2
        ny = min(max(y, min_y), max_y) if min_y <= max_y else (top + bottom) / 2
        if nx == x and ny == y:
            return
        vx, vy = body.velocity
        bounce = body.velocity.length >= 45
        if (x < nx and vx < 0) or (x > nx and vx > 0):
            vx *= -WALL_RESTITUTION if bounce else 0
        if (y < ny and vy < 0) or (y > ny and vy > 0):
            vy *= -(FLOOR_RESTITUTION if y > ny else WALL_RESTITUTION) if bounce else 0
        body.activate()
        body.position = nx, ny
        body.velocity = vx, vy
        self.spaces[entry.area].reindex_shapes_for_body(body)

    @staticmethod
    def _wake(space):
        for body in space.bodies:
            if body.body_type == pymunk.Body.DYNAMIC:
                body.activate()

    def remove(self, pet):
        entry = self.particles.pop(pet, None)
        if entry is not None:
            space = self.spaces[entry.area]
            space.remove(entry.shape, entry.body)
            self._wake(space)
        for other in self.particles.values():
            if other.source is pet:
                other.source = None

    def is_dynamic(self, pet):
        entry = self.particles.get(pet)
        return entry is not None and entry.body.body_type == pymunk.Body.DYNAMIC

    def is_settled(self, pet):
        """Visible rest needs support and low speed, without waiting for sleep."""
        entry = self.particles.get(pet)
        if entry is None or entry.body.is_sleeping:
            return True
        if entry.body.velocity.length > 10:
            return False
        supported = False

        def check_support(arbiter):
            nonlocal supported
            a, b = arbiter.shapes
            normal = arbiter.normal
            # Screen coordinates point down; the contact must support from below.
            below = normal.y if a is entry.shape else -normal.y
            other = b if a is entry.shape else a
            if below > 0.5 and (entry.body.velocity - other.body.velocity).length <= 10:
                supported = True

        entry.body.each_arbiter(check_support)
        return supported

    def sync_hover(self, pet, origin):
        entry = self.particles.get(pet)
        if entry is None or self.is_dynamic(pet):
            return
        position = (origin[0] + entry.offset[0], origin[1] + entry.offset[1])
        if (entry.body.position - position).length > 0.5:
            entry.body.position = position
            space = self.spaces[entry.area]
            space.reindex_shapes_for_body(entry.body)
            self._wake(space)

    def advance(self, elapsed):
        # Drop excess catch-up time after sleep; never take a large simulation step.
        self.accumulator = min(self.accumulator + max(0, elapsed) * MOTION_RATE, STEP * 8)
        while self.accumulator >= STEP - 1e-12:
            for entry in self.particles.values():
                if entry.source is not None:
                    source = self.particles.get(entry.source)
                    if source is None or source.area != entry.area or not self._capsules_overlap(entry, source):
                        entry.source = None
                if entry.body.body_type == pymunk.Body.DYNAMIC:
                    speed = entry.body.velocity.length
                    limit = 1800 * entry.speed_multiplier * (entry.area[3] - entry.area[1]) / 1080
                    if speed > limit:
                        entry.body.velocity *= limit / speed
            for space in self.spaces.values():
                space.step(STEP)
            for entry in self.particles.values():
                self._contain(entry)
            self.accumulator -= STEP

    @staticmethod
    def _capsules_overlap(a, b):
        # Both capsules are upright. Avoid Pymunk 7.3's zero-contact query assertion.
        delta = a.body.position - b.body.position
        gap = max(0, abs(delta.y) - abs(a.shape.b.y) - abs(b.shape.b.y))
        return delta.x ** 2 + gap ** 2 < (a.shape.radius + b.shape.radius) ** 2

    def positions(self):
        for pet, entry in tuple(self.particles.items()):
            if self.is_dynamic(pet) and not entry.body.is_sleeping:
                yield pet, (entry.body.position.x - entry.offset[0], entry.body.position.y - entry.offset[1])

    @staticmethod
    def launch_velocity(area, direction, rng=random):
        factor = (area[3] - area[1]) / 1080
        speed = rng.uniform(900, 1250) * factor
        angle = math.radians(rng.uniform(60, 80))
        return direction * speed * math.cos(angle), -speed * math.sin(angle)

    def clear(self):
        self.particles.clear()
        self.spaces.clear()
        self.accumulator = 0
