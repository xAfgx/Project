"""
ARES Human Behavior Cloning Module
==================================
Implements biologically realistic human interaction patterns for automated browser sessions.

Key research-backed principles:
1. Bezier curves with biological micro-jitter (not mathematical perfect curves)
2. Variable inter-keystroke delays (80-250ms)
3. Think pauses before critical interactions
4. Natural acceleration/deceleration profiles
5. Micro-corrections during movement
6. Human typing rhythm (burst-pause-burst patterns)
7. Hover patterns with purposeful exploration
8. Scroll physics with momentum and rubber-banding

Based on:
- Fitts' Law of human movement
- Human motor control biomechanics (Handbook of Brain Computation, 2024)
- DataDome behavioral analysis research (2026)
- PerimeterX mouse dynamics research (HUMAN Security, 2026)
"""

import math
import random
import time
from dataclasses import dataclass, field
from typing import List, Tuple, Optional


@dataclass
class BezierPoint:
    x: float
    y: float
    t: float  # normalized time
    velocity: float = 0.0


@dataclass
class MovementProfile:
    start: Tuple[float, float]
    end: Tuple[float, float]
    duration_ms: float
    bezier_points: List[BezierPoint] = field(default_factory=list)
    micro_corrections: List[Tuple[float, float]] = field(default_factory=list)
    think_pause_before: float = 0.0
    think_pause_after: float = 0.0


@dataclass
class TypingPattern:
    characters: str
    delays: List[float] = field(default_factory=list)
    hold_durations: List[float] = field(default_factory=list)
    burst_patterns: List[int] = field(default_factory=list)


class BiologicalMovementGenerator:
    """
    Generates biologically realistic mouse movement paths using modified Bezier curves.
    
    Real human mouse movements are NOT perfect Bezier curves. They exhibit:
    1. Micro-corrections (small zigzags during movement)
    2. Acceleration curves that don't follow simple ease-in/ease-out
    3. Sudden velocity changes (overshoot corrections)
    4. Stopping oscillation near the target
    5. Variable speed based on distance and target size (Fitts' Law)
    """

    def __init__(self, seed: Optional[int] = None):
        self._rng = random.Random(seed)
        # Human motor parameters (from motor control research)
        self.max_acceleration = 12000  # pixels/s² (typical human)
        self.max_velocity = 1500       # pixels/s (typical human)
        self.min_velocity = 5          # pixels/s (micro-corrections)
        self.settling_threshold = 2.0  # pixels (stop when close enough)

    def generate_movement_path(
        self,
        start_x: float,
        start_y: float,
        end_x: float,
        end_y: float,
        duration_ms: Optional[float] = None,
        target_size: float = 20.0
    ) -> List[Tuple[float, float]]:
        """
        Generate a biologically realistic mouse movement path.
        
        Args:
            start_x, start_y: Starting coordinates
            end_x, end_y: Target coordinates
            duration_ms: Movement duration (auto-calculated if None using Fitts' Law)
            target_size: Size of target in pixels (affects movement time)
            
        Returns:
            List of (x, y) coordinate tuples representing the movement path
        """
        dx = end_x - start_x
        dy = end_y - start_y
        distance = math.sqrt(dx * dx + dy * dy)

        if distance < 1.0:
            return [(start_x, start_y)]

        # Fitts' Law: MT = a + b * log2(D/W + 1)
        # Where D = distance, W = target width
        if duration_ms is None:
            a_ms = 200  # motor preparation time
            b_ms = 100  # movement rate constant
            duration_ms = a_ms + b_ms * math.log2(distance / target_size + 1)

        # Add biological variation to duration (±15%)
        duration_ms *= self._rng.uniform(0.85, 1.15)

        # Generate base Bezier curve (not perfect - add biological noise)
        num_control_points = max(3, int(distance / 30))
        control_points = self._generate_control_points(
            start_x, start_y, end_x, end_y, num_control_points
        )

        # Sample the Bezier curve with biological noise
        num_samples = max(10, int(duration_ms / 16))  # ~60fps sampling
        path = []
        for i in range(num_samples + 1):
            t = i / num_samples
            # Add biological noise to parameter t (humans don't move at constant rate)
            t_noisy = t + self._rng.gauss(0, 0.02) * math.sin(t * math.pi)
            t_noisy = max(0, min(1, t_noisy))

            x, y = self._bezier_point(control_points, t_noisy)

            # Add micro-jitter (biological tremor)
            tremor_amplitude = 1.5 if distance > 100 else 0.5
            x += self._rng.gauss(0, tremor_amplitude)
            y += self._rng.gauss(0, tremor_amplitude)

            # Add overshoot correction (humans overshoot and correct)
            overshoot = self._calculate_overshoot(t, distance)
            if overshoot != 0:
                x += overshoot * dx / distance * 2
                y += overshoot * dy / distance * 2

            path.append((x, y))

        # Add micro-corrections during approach (last 20% of movement)
        if len(path) > 5:
            correction_start = int(len(path) * 0.8)
            for i in range(correction_start, len(path)):
                if i + 1 < len(path):
                    # Small random adjustments
                    path[i] = (
                        path[i][0] + self._rng.gauss(0, 0.8),
                        path[i][1] + self._rng.gauss(0, 0.8)
                    )

        # Add stopping oscillation (humans oscillate when stopping)
        if len(path) > 3:
            final_t = path[-1]
            for i in range(3):
                oscillation = self._rng.gauss(0, 0.5) * math.exp(-i * 0.5)
                path.append((
                    final_t[0] + oscillation,
                    final_t[1] + oscillation
                ))

        return path

    def _generate_control_points(
        self,
        sx: float, sy: float,
        ex: float, ey: float,
        num_points: int
    ) -> List[Tuple[float, float]]:
        """Generate control points for a biologically realistic Bezier curve."""
        dx = ex - sx
        dy = ey - sy
        points = [(sx, sy)]

        for i in range(1, num_points - 1):
            t = i / (num_points - 1)
            # Humans don't move in straight lines - slight curves
            # S-curve for longer movements, more direct for short ones
            curve_factor = math.sin(t * math.pi) * (0.3 if abs(dx) + abs(dy) > 200 else 0.1)
            # Add biological randomness to curve direction
            lateral_shift = self._rng.gauss(0, 15) * math.sin(t * math.pi * 2)
            px = sx + dx * t + curve_factor * dy + lateral_shift
            py = sy + dy * t - curve_factor * dx + lateral_shift * 0.5
            points.append((px, py))

        points.append((ex, ey))
        return points

    def _bezier_point(
        self,
        points: List[Tuple[float, float]],
        t: float
    ) -> Tuple[float, float]:
        """Calculate point on Bezier curve using De Casteljau's algorithm."""
        n = len(points)
        if n < 2:
            return points[0]

        # De Casteljau's algorithm
        temp = list(points)
        for level in range(n - 1):
            for i in range(n - 1 - level):
                x = temp[i][0] * (1 - t) + temp[i + 1][0] * t
                y = temp[i][1] * (1 - t) + temp[i + 1][1] * t
                temp[i] = (x, y)

        return temp[0]

    def _calculate_overshoot(self, t: float, distance: float) -> float:
        """Calculate overshoot correction based on movement progress."""
        if t < 0.3 or t > 0.8:
            return 0
        # Humans overshoot slightly near the end and correct
        overshoot_pattern = math.sin((t - 0.3) / 0.5 * math.pi) * 0.08
        return overshoot_pattern


class HumanTypingSimulator:
    """
    Simulates human typing patterns with realistic inter-keystroke delays.
    
    Research shows:
    - Average typing speed: 40-60 WPM (67-100 chars/min)
    - Inter-keystroke intervals follow a gamma distribution
    - Burst-pause patterns: humans type in bursts of 3-7 characters then pause
    - Character-specific delays: punctuation and special chars have longer delays
    - Error correction: humans sometimes backspace and retype
    """

    # Typical inter-keystroke delays (ms) by character category
    KEY_DELAY_PROFILES = {
        "alphanumeric": (60, 180),    # Mean: 120ms
        "punctuation": (100, 250),     # Mean: 175ms
        "space": (150, 300),           # Mean: 225ms
        "special": (200, 400),         # Mean: 300ms
        "navigate": (50, 150),         # Mean: 100ms (arrow keys, tab)
    }

    def __init__(self, seed: Optional[int] = None):
        self._rng = random.Random(seed)

    def generate_typing_pattern(self, text: str) -> List[Tuple[str, float]]:
        """
        Generate a human-like typing pattern for the given text.
        
        Args:
            text: Text to "type"
            
        Returns:
            List of (character, delay_ms) tuples
        """
        result = []
        burst_count = 0
        burst_length = 0

        for char in text:
            # Determine delay category
            delay_category = self._get_delay_category(char)
            min_delay, max_delay = self.KEY_DELAY_PROFILES[delay_category]

            # Add burst-pause pattern
            burst_length += 1
            if burst_length >= 5 and self._rng.random() < 0.3:
                # Pause between bursts
                pause = self._rng.gamma(2, 80)  # Mean: 160ms, right-skewed
                result.append(('\b', pause))  # Small backspace effect
                burst_length = 0
                burst_count += 1

            # Gamma-distributed delay (more realistic than uniform)
            delay = self._rng.gamma(2, max_delay / 2)
            delay = max(min_delay, min(max_delay, delay))

            # Add hold duration (key press duration)
            hold = self._rng.uniform(30, 120)

            result.append((char, delay))

        return result

    def _get_delay_category(self, char: str) -> str:
        """Classify character into delay category."""
        if char == ' ':
            return 'space'
        if char.isalnum():
            return 'alphanumeric'
        if char in '.,!?;:"\'-–—':
            return 'punctuation'
        if char in '\t\n\r':
            return 'navigate'
        return 'special'

    def simulate_typing(
        self,
        page,
        locator,
        text: str,
        think_pause_before_ms: int = 500
    ):
        """
        Type text into a field with human-like timing.
        
        Args:
            page: Browser page object
            locator: Target input locator
            text: Text to type
            think_pause_before_ms: Pause before starting to type
        """
        import asyncio
        import time as time_mod

        # Think pause before typing
        if think_pause_before_ms > 0:
            actual_pause = think_pause_before_ms + self._rng.randint(-200, 300)
            actual_pause = max(0, actual_pause)
            time_mod.sleep(actual_pause / 1000)

        # Click on the field first (with human-like movement)
        self._human_click(page, locator)

        # Focus the input
        locator.focus()

        # Generate and execute typing pattern
        pattern = self.generate_typing_pattern(text)
        for char, delay_ms in pattern:
            if char == '\b':
                # Backspace effect (partial)
                page.keyboard.press('Backspace')
            else:
                page.keyboard.type(char, delay=int(delay_ms * 0.1))

            # Actual delay between keystrokes
            time_mod.sleep(delay_ms / 1000)

    def _human_click(self, page, locator):
        """Click with human-like delay before the action."""
        time_mod.sleep(self._rng.uniform(100, 400) / 1000)
        locator.click()


class ThinkPauseManager:
    """
    Manages realistic think/hesitation pauses before critical interactions.
    
    Humans pause before:
    - Clicking important buttons (purchase, submit, etc.)
    - Reading text before action
    - Making decisions at decision points
    - Entering forms they haven't seen before
    
    Pause durations follow a log-normal distribution:
    - Quick decisions: 200-600ms
    - Moderate decisions: 600-1500ms
    - Important decisions (checkout, purchase): 1500-3000ms
    """

    DECISION_PHASES = {
        "quick": (150, 600),       # Simple navigation clicks
        "moderate": (400, 1200),   # Form field interactions
        "important": (1200, 3000), # Checkout, purchase, submit
        "critical": (2000, 5000),  # Final payment, order confirmation
    }

    def __init__(self, seed: Optional[int] = None):
        self._rng = random.Random(seed)

    def think_pause(self, phase: str = "moderate") -> float:
        """
        Generate a realistic think pause duration.
        
        Args:
            phase: Decision importance level
            
        Returns:
            Pause duration in milliseconds
        """
        min_ms, max_ms = self.DECISION_PHASES.get(phase, self.DECISION_PHASES["moderate"])
        # Use log-normal distribution for more realistic pause times
        mean_log = math.log((min_ms + max_ms) / 2)
        sigma = 0.5
        pause = math.exp(self._rng.gauss(mean_log, sigma))
        return max(min_ms, min(max_ms, pause))

    def apply_pause(self, phase: str = "moderate"):
        """Sleep for the calculated think pause duration."""
        pause_ms = self.think_pause(phase)
        time.sleep(pause_ms / 1000)
        return pause_ms


class BehavioralProfile:
    """
    Complete behavioral profile combining all human-like behaviors.
    
    Integrates movement, typing, think pauses, and scroll patterns.
    All behaviors are seeded per-session for reproducibility but vary per session.
    """

    def __init__(self, session_seed: Optional[int] = None):
        self.session_seed = session_seed or random.randint(0, 2**31)
        self.movement = BiologicalMovementGenerator(self.session_seed)
        self.typing = HumanTypingSimulator(self.session_seed + 1)
        self.pauses = ThinkPauseManager(self.session_seed + 2)
        self._rng = random.Random(self.session_seed)

    def scroll_behavior(self, page, scroll_amount: int, container=None):
        """
        Simulate human-like scrolling with momentum and natural deceleration.
        
        Humans scroll in bursts with variable speed, not constant velocity.
        They also "rubber-band" slightly when reaching scroll limits.
        """
        if container:
            element = container
        else:
            element = page

        # Scroll in multiple small increments with variable speed
        total_scrolled = 0
        chunk_size = self._rng.randint(50, 150)
        while total_scrolled < abs(scroll_amount):
            chunk = min(chunk_size, abs(scroll_amount) - total_scrolled)
            direction = 1 if scroll_amount > 0 else -1

            # Variable speed: sometimes fast, sometimes slow
            speed = self._rng.uniform(0.5, 2.0)
            element.mouse.wheel(direction * chunk * speed)

            total_scrolled += chunk

            # Natural pause between scroll bursts
            pause_ms = self._rng.randint(20, 150)
            time.sleep(pause_ms / 1000)

    def random_movement(self, page, bounds: dict):
        """
        Generate a random human-like mouse movement within bounds.
        
        Args:
            page: Browser page
            bounds: {'x': min, 'y': min, 'width': w, 'height': h}
        """
        start_x = bounds['x'] + self._rng.uniform(0, bounds['width'] * 0.3)
        start_y = bounds['y'] + self._rng.uniform(0, bounds['height'] * 0.3)
        end_x = bounds['x'] + self._rng.uniform(bounds['width'] * 0.2, bounds['width'] * 0.9)
        end_y = bounds['y'] + self._rng.uniform(bounds['height'] * 0.2, bounds['height'] * 0.9)

        duration = self._rng.randint(300, 1500)
        path = self.movement.generate_movement_path(
            start_x, start_y, end_x, end_y, duration_ms=duration
        )

        # Execute the path via CDP or page.mouse
        for i, (x, y) in enumerate(path):
            if i == 0:
                page.mouse.move(x, y)
            else:
                page.mouse.move(x, y)
            time.sleep(self._rng.uniform(1, 16) / 1000)  # ~60fps


def create_human_session(session_seed: Optional[int] = None) -> BehavioralProfile:
    """
    Factory function to create a human behavioral profile.
    
    Args:
        session_seed: Optional seed for reproducible session behavior
        
    Returns:
        BehavioralProfile configured for human-like interaction
    """
    return BehavioralProfile(session_seed)


# Quick utility functions
def human_delay(min_ms: int = 80, max_ms: int = 250) -> float:
    """Generate a human-like delay between actions using gamma distribution."""
    rng = random.Random()
    delay = rng.gamma(2, (max_ms - min_ms) / 2) + min_ms
    return max(min_ms, min(max_ms, delay))


def think_pause(phase: str = "moderate") -> float:
    """Generate a realistic think pause."""
    manager = ThinkPauseManager()
    return manager.think_pause(phase)


if __name__ == "__main__":
    # Test the behavior module
    profile = create_human_session(42)

    # Test movement generation
    path = profile.movement.generate_movement_path(100, 100, 500, 300, duration_ms=800)
    print(f"Generated {len(path)} movement points")
    print(f"  Start: ({path[0][0]:.1f}, {path[0][1]:.1f})")
    print(f"  End: ({path[-1][0]:.1f}, {path[-1][1]:.1f})")

    # Test typing pattern
    pattern = profile.typing.generate_typing_pattern("Hello World!")
    total_delay = sum(d for _, d in pattern)
    print(f"\nTyping pattern: {len(pattern)} keystrokes")
    print(f"  Total delay: {total_delay:.0f}ms")

    # Test think pauses
    quick = profile.pauses.think_pause("quick")
    important = profile.pauses.think_pause("important")
    critical = profile.pauses.think_pause("critical")
    print(f"\nThink pauses:")
    print(f"  Quick: {quick:.0f}ms")
    print(f"  Important: {important:.0f}ms")
    print(f"  Critical: {critical:.0f}ms")

    print("\nHuman behavior module ready.")
