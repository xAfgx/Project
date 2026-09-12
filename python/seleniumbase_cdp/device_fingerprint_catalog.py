from __future__ import annotations

import json
import random

"""Static, real-world device value catalog for seeded fingerprint diversity.

Every entry in this catalog is a value that exists on real consumer devices.
The spoof runtime must always pick a FULLY CONSISTENT tuple from one OS family
(user agent platform <-> WebGL ANGLE renderer <-> screen <-> fonts <-> locale),
never a random cross-family mix. A mix of OS families is instantly flagged by
browserleaks and by any serious WAF.

The catalog is pure data plus one pure helper. It imports nothing from ARES and
has no side effects, so it can be embedded in either task worker without risk.
"""

# ---------------------------------------------------------------------------
# OS families
# ---------------------------------------------------------------------------

OS_WINDOWS = "windows"
OS_MACOS = "macos"

# ---------------------------------------------------------------------------
# WebGL GPU entries. Each entry is the COMPLETE set of the four values that a
# real Chrome instance reports for getParameter() queries:
#
#   gl.VENDOR            (param 7936)  -> vendor
#   gl.RENDERER          (param 7937)  -> renderer (full ANGLE string)
#   UNMASKED_VENDOR_WEBGL   (37445)    -> unmasked_vendor
#   UNMASKED_RENDERER_WEBGL (37446)    -> unmasked_renderer
#
# The renderer string must stay in the exact ANGLE format Chrome uses on that
# OS, otherwise browserleaks.com/webgl flags the device as spoofed.
# ---------------------------------------------------------------------------

WINDOWS_GPUS = (
    {
        "vendor": "Google Inc. (NVIDIA)",
        "renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)",
        "unmasked_vendor": "NVIDIA Corporation",
        "unmasked_renderer": "NVIDIA GeForce RTX 3060",
    },
    {
        "vendor": "Google Inc. (NVIDIA)",
        "renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Laptop GPU Direct3D11 vs_5_0 ps_5_0, D3D11)",
        "unmasked_vendor": "NVIDIA Corporation",
        "unmasked_renderer": "NVIDIA GeForce RTX 3060 Laptop GPU",
    },
    {
        "vendor": "Google Inc. (NVIDIA)",
        "renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 3070 Direct3D11 vs_5_0 ps_5_0, D3D11)",
        "unmasked_vendor": "NVIDIA Corporation",
        "unmasked_renderer": "NVIDIA GeForce RTX 3070",
    },
    {
        "vendor": "Google Inc. (NVIDIA)",
        "renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 4060 Laptop GPU Direct3D11 vs_5_0 ps_5_0, D3D11)",
        "unmasked_vendor": "NVIDIA Corporation",
        "unmasked_renderer": "NVIDIA GeForce RTX 4060 Laptop GPU",
    },
    {
        "vendor": "Google Inc. (NVIDIA)",
        "renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 Direct3D11 vs_5_0 ps_5_0, D3D11)",
        "unmasked_vendor": "NVIDIA Corporation",
        "unmasked_renderer": "NVIDIA GeForce RTX 4070",
    },
    {
        "vendor": "Google Inc. (NVIDIA)",
        "renderer": "ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 Ti Direct3D11 vs_5_0 ps_5_0, D3D11)",
        "unmasked_vendor": "NVIDIA Corporation",
        "unmasked_renderer": "NVIDIA GeForce GTX 1660 Ti",
    },
    {
        "vendor": "Google Inc. (Intel)",
        "renderer": "ANGLE (Intel, Intel(R) Iris(R) Xe Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)",
        "unmasked_vendor": "Intel",
        "unmasked_renderer": "Intel(R) Iris(R) Xe Graphics",
    },
    {
        "vendor": "Google Inc. (Intel)",
        "renderer": "ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)",
        "unmasked_vendor": "Intel",
        "unmasked_renderer": "Intel(R) UHD Graphics 630",
    },
    {
        "vendor": "Google Inc. (AMD)",
        "renderer": "ANGLE (AMD, AMD Radeon(TM) Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)",
        "unmasked_vendor": "Advanced Micro Devices, Inc.",
        "unmasked_renderer": "AMD Radeon(TM) Graphics",
    },
    {
        "vendor": "Google Inc. (AMD)",
        "renderer": "ANGLE (AMD, AMD Radeon RX 6600 Direct3D11 vs_5_0 ps_5_0, D3D11)",
        "unmasked_vendor": "Advanced Micro Devices, Inc.",
        "unmasked_renderer": "AMD Radeon RX 6600",
    },
)

MACOS_GPUS = (
    {
        "vendor": "Google Inc. (Apple)",
        "renderer": "ANGLE (Apple, Apple M1, OpenGL 4.1)",
        "unmasked_vendor": "Apple",
        "unmasked_renderer": "Apple M1",
    },
    {
        "vendor": "Google Inc. (Apple)",
        "renderer": "ANGLE (Apple, Apple M2, OpenGL 4.1)",
        "unmasked_vendor": "Apple",
        "unmasked_renderer": "Apple M2",
    },
    {
        "vendor": "Google Inc. (Apple)",
        "renderer": "ANGLE (Apple, Apple M3, OpenGL 4.1)",
        "unmasked_vendor": "Apple",
        "unmasked_renderer": "Apple M3",
    },
    {
        "vendor": "Google Inc. (Intel)",
        "renderer": "ANGLE (Intel, Intel(R) Iris(TM) Plus Graphics, OpenGL 4.1)",
        "unmasked_vendor": "Intel Inc.",
        "unmasked_renderer": "Intel(R) Iris(TM) Plus Graphics",
    },
    {
        "vendor": "Google Inc. (AMD)",
        "renderer": "ANGLE (AMD, AMD Radeon Pro 5500M OpenGL Engine, OpenGL 4.1)",
        "unmasked_vendor": "AMD",
        "unmasked_renderer": "AMD Radeon Pro 5500M",
    },
)

GPUS_BY_OS = {
    OS_WINDOWS: WINDOWS_GPUS,
    OS_MACOS: MACOS_GPUS,
}

# ---------------------------------------------------------------------------
# Real consumer screen resolutions per OS family. Screen resolution must be
# >= window size at all times; the spoof runtime picks the window below this.
# ---------------------------------------------------------------------------

WINDOWS_RESOLUTIONS = (
    (1920, 1080),
    (2560, 1440),
    (1920, 1200),
    (1680, 1050),
    (1600, 900),
    (1536, 864),
    (1366, 768),
    (1440, 900),
    (3840, 2160),
)

MACOS_RESOLUTIONS = (
    (1440, 900),
    (1680, 1050),
    (2560, 1600),
    (3024, 1964),
    (3072, 1920),
)

RESOLUTIONS_BY_OS = {
    OS_WINDOWS: WINDOWS_RESOLUTIONS,
    OS_MACOS: MACOS_RESOLUTIONS,
}

# Window sizes stay slightly below the screen resolution (browser chrome).
WINDOW_INSET = (12, 82)

# ---------------------------------------------------------------------------
# Plausible hardwareConcurrency <-> deviceMemory PAIRS. Picking them as pairs
# prevents impossible devices such as "16 cores with 0.5 GB RAM".
# ---------------------------------------------------------------------------

HW_MEM_PAIRS = (
    (4, 4),
    (8, 8),
    (6, 8),
    (12, 16),
    (16, 16),
    (8, 4),
    (16, 32),
    (10, 16),
)

DEVICE_PIXEL_RATIOS = (1.0, 1.25, 1.5, 2.0)

# ---------------------------------------------------------------------------
# Real font stacks per OS family. Fonts are read from the OS, so the spoof
# runtime must keep the family aligned with the machine that really runs the
# browser. Cross-family font spoofing is unstable and detectable; the catalog
# therefore provides the honest lists, not fake detection of foreign fonts.
# ---------------------------------------------------------------------------

WINDOWS_FONTS = (
    "Arial", "Arial Black", "Bahnschrift", "Calibri", "Cambria",
    "Comic Sans MS", "Consolas", "Courier New", "Ebrima", "Franklin Gothic Medium",
    "Gabriola", "Georgia", "Impact", "Ink Free", "Javanese Text",
    "Lucida Console", "Lucida Sans Unicode", "Microsoft Sans Serif", "MS Gothic",
    "Segoe Print", "Segoe Script", "Segoe UI", "Segoe UI Emoji", "Sitka",
    "Tahoma", "Times New Roman", "Trebuchet MS", "Verdana", "Webdings",
)

MACOS_FONTS = (
    "American Typewriter", "Andale Mono", "Apple Chancery", "Arial",
    "Arial Black", "Arial Narrow", "Arial Rounded MT Bold", "Baskerville",
    "Big Caslon", "Brush Script MT", "Chalkboard", "Cochin", "Comic Sans MS",
    "Copperplate", "Courier", "Courier New", "Didot", "Futura", "Geneva",
    "Georgia", "Gill Sans", "Helvetica", "Helvetica Neue", "Impact",
    "Lucida Grande", "Marker Felt", "Menlo", "Monaco", "Optima", "Palatino",
    "Papyrus", "Skia", "Tahoma", "Times", "Times New Roman", "Trebuchet MS",
    "Verdana", "Zapfino",
)

FONTS_BY_OS = {
    OS_WINDOWS: WINDOWS_FONTS,
    OS_MACOS: MACOS_FONTS,
}

# ---------------------------------------------------------------------------
# Locale bundles keyed by country. The runtime must align timezone + locale +
# Accept-Language with the proxy IP country, otherwise the layers contradict
# each other. German-speaking countries first (primary target region).
# ---------------------------------------------------------------------------

LOCALE_BUNDLES = {
    "DE": {
        "timezone": "Europe/Berlin",
        "locale": "de-DE",
        "acceptLanguage": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
        "languages": ("de-DE", "de", "en-US", "en"),
    },
    "AT": {
        "timezone": "Europe/Vienna",
        "locale": "de-AT",
        "acceptLanguage": "de-AT,de;q=0.9,en-US;q=0.8,en;q=0.7",
        "languages": ("de-AT", "de", "en-US", "en"),
    },
    "CH": {
        "timezone": "Europe/Zurich",
        "locale": "de-CH",
        "acceptLanguage": "de-CH,de;q=0.9,en-US;q=0.8,en;q=0.7",
        "languages": ("de-CH", "de", "en-US", "en"),
    },
}

DEFAULT_LOCALE_BUNDLE = LOCALE_BUNDLES["DE"]

# ---------------------------------------------------------------------------
# Helpers (pure functions, no side effects)
# ---------------------------------------------------------------------------


def os_family_from_user_agent(user_agent: str) -> str:
    """Map a user agent string to the OS family used by this catalog."""
    ua = str(user_agent or "").lower()
    if "windows" in ua or "win64" in ua or "win32" in ua:
        return OS_WINDOWS
    if "macintosh" in ua or "mac os x" in ua or "iphone" in ua or "ipad" in ua:
        return OS_MACOS
    return OS_WINDOWS


def gpus_for_user_agent(user_agent: str) -> tuple[dict, ...]:
    return GPUS_BY_OS.get(os_family_from_user_agent(user_agent), WINDOWS_GPUS)


def build_device_fingerprint_script(seed: int, user_agent: str | None = None) -> str:
    """Build the deterministic browser fingerprint spoof script for one seed.

    Returns the JavaScript IIFE injected via Page.addScriptToEvaluateOnNewDocument.
    The same seed always yields the same device (stable fingerprint); different
    seeds yield different consistent devices (cluster diversity). This is the
    single source of truth shared by the task worker and the manual profile
    browser, so both paths expose identical spoof logic.
    """
    rng = random.Random(int(seed) if seed else 1)
    gpu = rng.choice(gpus_for_user_agent(user_agent or ""))
    hw, mem = rng.choice(HW_MEM_PAIRS)
    device = json.dumps({
        "seed": int(seed) if seed else 1,
        "hw": int(hw),
        "mem": int(mem),
        "gpuVendor": str(gpu.get("vendor") or ""),
        "gpuRenderer": str(gpu.get("renderer") or ""),
        "gpuUnmaskedVendor": str(gpu.get("unmasked_vendor") or ""),
        "gpuUnmaskedRenderer": str(gpu.get("unmasked_renderer") or ""),
    })
    return r"""
(() => {
  // Worker-safe global: workers have no window/document. The WebGL patch must
  // run in workers too, otherwise they report the real GPU while the page
  // reports the catalog GPU (anti-bot checks flag that as rewritten).
  const G = (typeof window !== "undefined") ? window : ((typeof self !== "undefined") ? self : globalThis);
  if (G.__aresRawCanvas) return;
  const D = __DEVICE__;
  const seed = D.seed || 1;
  const guard = fn => { try { fn(); } catch (_) {} };

  // Patch through the navigator instance's prototype: this resolves to
  // Navigator.prototype on the page and WorkerNavigator.prototype in a Worker,
  // where the `Navigator` global does not exist. The getter throws on a foreign
  // `this` like the native accessor, so accessor-tamper probes do not flag it.
  const navProto = Object.getPrototypeOf(navigator);
  const hardenAccessor = (name, value) => guard(() => Object.defineProperty(navProto, name, {
    get: function () {
      if (this !== navigator && Object.getPrototypeOf(this) !== navProto) {
        throw new TypeError("Illegal invocation");
      }
      return value;
    },
    configurable: true,
  }));
  hardenAccessor('hardwareConcurrency', D.hw);
  hardenAccessor('deviceMemory', D.mem);

  // NOTE: screen, devicePixelRatio and getBoundingClientRect stay REAL.
  // They are used by SeleniumBase's own click/scroll coordinate math, and any
  // divergence from the physical screen makes every click land off-target.
  // Fingerprint diversity comes from WebGL strings and canvas noise instead.

  // WebGL: patch ALL FOUR parameters Chrome reports, from one GPU entry, so
  // vendor (7936), renderer (7937) and the unmasked pair (37445/37446) always
  // describe the same real device in the exact ANGLE format Chrome uses.
  // Real Chrome reports GENERIC masked values and the real ANGLE string as the
  // UNMASKED pair:
  //   7936 VENDOR                  -> "WebKit"
  //   7937 RENDERER                -> "WebKit WebGL"
  //   37445 UNMASKED_VENDOR_WEBGL  -> "Google Inc. (Intel)"
  //   37446 UNMASKED_RENDERER_WEBGL-> "ANGLE (Intel, Intel(R) Iris(R) Xe ...)"
  // Putting the ANGLE string in the masked slot is what anti-bot checks flag as
  // "masked mentions ANGLE but unmasked does not".
  const patchGL = proto => {
    const orig = proto.getParameter;
    proto.getParameter = function (p) {
      if (p === 7936) return "WebKit";
      if (p === 7937) return "WebKit WebGL";
      if (p === 37445) return D.gpuVendor;
      if (p === 37446) return D.gpuRenderer;
      return orig.apply(this, arguments);
    };
  };
  guard(() => patchGL(WebGLRenderingContext.prototype));
  guard(() => patchGL(WebGL2RenderingContext.prototype));

  // WebGPU must report the same GPU family as WebGL. Anti-bot checks compare
  // the two APIs; a spoofed WebGL against the real WebGPU adapter is flagged as
  // "driver string rewritten".
  guard(() => {
    if (typeof GPUAdapter === "undefined" || !GPUAdapter.prototype) return;
    const descriptor = Object.getOwnPropertyDescriptor(GPUAdapter.prototype, "info");
    if (!descriptor || typeof descriptor.get !== "function") return;
    const unmasked = String(D.gpuUnmaskedVendor || "").toLowerCase();
    const vendor = unmasked.includes("nvidia") ? "nvidia" : unmasked.includes("intel") ? "intel" : unmasked.includes("amd") ? "amd" : "unknown";
    const architecture = vendor === "intel" ? "gen-12lp" : vendor === "nvidia" ? "ampere" : vendor === "amd" ? "rdna-2" : "";
    Object.defineProperty(GPUAdapter.prototype, "info", {
      get: function () {
        const info = descriptor.get.call(this);
        try {
          return new Proxy(info, {
            get(target, prop) {
              if (prop === "vendor") return vendor;
              if (prop === "architecture") return architecture;
              if (prop === "device") return "";
              if (prop === "description") return "";
              return target[prop];
            },
          });
        } catch (_) {
          return info;
        }
      },
      configurable: true,
    });
  });

  // Deterministic canvas fingerprint noise. The same seed + pixel + channel
  // always yields the same bit, so the fingerprint is STABLE within a profile
  // yet unique across profiles. The previous rand()-based jitter changed on
  // every call, which is itself a fingerprinting red flag.
  const noiseBit = (x, y, ch) => {
    let h = (seed ^ (x * 0x9E3779B9) ^ (y * 0x85EBCA6B) ^ (ch * 0xC2B2AE35)) >>> 0;
    h = (h ^ (h >>> 13)) >>> 0;
    h = (h * 0x2545F491) >>> 0;
    h = (h ^ (h >>> 16)) >>> 0;
    return h & 1;
  };
  // The transform SETS the LSB instead of adding +/-1. That makes it
  // idempotent: a page that reads its own canvas back, writes the data again
  // and re-reads it (reCAPTCHA composes its challenge tiles exactly like that)
  // cannot accumulate the noise into a visible dither pattern. A single
  // application still shifts each channel by at most 1 LSB, which is invisible
  // to the eye and keeps the canvas fingerprint unique per profile.
  const noiseImageData = (data, ox, oy, width) => {
    const px = data.data;
    const pixels = data.width * data.height;
    for (let i = 0; i < pixels; i++) {
      const p = i * 4;
      const x = ox + (i % width);
      const y = oy + ((i / width) | 0);
      px[p] = (px[p] & 0xFE) | noiseBit(x, y, 0);
      px[p + 1] = (px[p + 1] & 0xFE) | noiseBit(x, y, 1);
      px[p + 2] = (px[p + 2] & 0xFE) | noiseBit(x, y, 2);
    }
    return data;
  };

  const hasCanvas2D = (typeof CanvasRenderingContext2D !== "undefined") && (typeof HTMLCanvasElement !== "undefined");
  if (!hasCanvas2D) return;

  const origGetImageData = CanvasRenderingContext2D.prototype.getImageData;
  const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
  const origToBlob = HTMLCanvasElement.prototype.toBlob;

  const noisedCanvas = (canvas) => {
    const w = canvas.width, h = canvas.height;
    const ctx = canvas.getContext('2d');
    if (!ctx || w <= 0 || h <= 0) return null;
    const data = origGetImageData.call(ctx, 0, 0, w, h);
    const off = document.createElement('canvas');
    off.width = w; off.height = h;
    off.getContext('2d').putImageData(noiseImageData(data, 0, 0, w), 0, 0);
    return off;
  };

  // Raw, un-noised API for ARES' own captcha tile extraction. The vision
  // classifier must read the exact pixels the challenge rendered, so the grid
  // adapters route through here instead of the noised public methods below.
  G.__aresRawCanvas = {
    toDataURL: (c, t, q) => origToDataURL.call(c, t, q),
    getImageData: (ctx, sx, sy, sw, sh) => origGetImageData.call(ctx, sx, sy, sw, sh),
  };

  CanvasRenderingContext2D.prototype.getImageData = function (sx, sy, sw, sh) {
    const d = origGetImageData.call(this, sx, sy, sw, sh);
    return noiseImageData(d, sx, sy, d.width);
  };

  HTMLCanvasElement.prototype.toDataURL = function (type, quality) {
    try {
      const off = noisedCanvas(this);
      if (off) return origToDataURL.call(off, type, quality);
    } catch (_) {}
    return origToDataURL.call(this, type, quality);
  };

  // toBlob must return the SAME pixels as toDataURL. Leaving it unpatched while
  // toDataURL is noised lets an anti-bot compare the two readbacks and detect
  // the spoof, so both go through the identical idempotent transform.
  HTMLCanvasElement.prototype.toBlob = function (callback, type, quality) {
    try {
      const off = noisedCanvas(this);
      if (off) return origToBlob.call(off, callback, type, quality);
    } catch (_) {}
    return origToBlob.call(this, callback, type, quality);
  };

  // Hide the patched natives from Function.prototype.toString introspection.
  // The stealth layer only overrides each function's own toString; anti-bot
  // probes call Function.prototype.toString.call(fn) directly.
  guard(() => {
    const nativeToString = Function.prototype.toString;
    const patched = new WeakSet();
    const markAccessor = (proto, name) => {
      try {
        const descriptor = Object.getOwnPropertyDescriptor(proto, name);
        if (descriptor && typeof descriptor.get === "function") patched.add(descriptor.get);
      } catch (_) {}
    };
    const markValue = (proto, name) => {
      try { if (proto && typeof proto[name] === "function") patched.add(proto[name]); } catch (_) {}
    };
    markAccessor(navProto, "hardwareConcurrency");
    markAccessor(navProto, "deviceMemory");
    if (typeof WebGLRenderingContext !== "undefined") markValue(WebGLRenderingContext.prototype, "getParameter");
    if (typeof WebGL2RenderingContext !== "undefined") markValue(WebGL2RenderingContext.prototype, "getParameter");
    if (typeof CanvasRenderingContext2D !== "undefined") markValue(CanvasRenderingContext2D.prototype, "getImageData");
    if (typeof HTMLCanvasElement !== "undefined") {
      markValue(HTMLCanvasElement.prototype, "toDataURL");
      markValue(HTMLCanvasElement.prototype, "toBlob");
    }
    const patchedToString = function () {
      if (patched.has(this)) return "function () { [native code] }";
      return nativeToString.call(this);
    };
    try { Object.defineProperty(patchedToString, "name", { value: "toString", configurable: true }); } catch (_) {}
    Object.defineProperty(Function.prototype, "toString", { value: patchedToString, configurable: true, writable: true });
  });
})()
""".replace("__DEVICE__", device)
