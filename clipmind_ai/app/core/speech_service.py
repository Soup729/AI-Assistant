import gc
import json
import os
import threading
import time
from collections import deque
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Deque, Dict, Optional, Tuple

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

import numpy as np

from app.storage.config import config_manager
from app.utils.logger import logger
from app.utils.runtime_paths import get_project_root, get_user_data_dir

try:
    import pyaudiowpatch as pyaudio
except ImportError:  # pragma: no cover - optional dependency in local dev
    pyaudio = None

try:
    import sherpa_onnx
except ImportError:  # pragma: no cover - optional dependency in local dev
    sherpa_onnx = None


TARGET_SAMPLE_RATE = 16000
DEFAULT_CHUNK_SIZE = 512
DEFAULT_MODEL_FOLDER = "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
DEFAULT_PARTIAL_DECODE_INTERVAL = 0.18


@dataclass
class _AudioDeviceSpec:
    index: int
    name: str
    sample_rate: int
    channels: int


class _RecordingSession:
    """
    Capture system audio + microphone and decode incrementally in background.
    """

    def __init__(
        self,
        recognizer,
        on_partial: Optional[Callable[[str], None]] = None,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        partial_decode_interval: float = DEFAULT_PARTIAL_DECODE_INTERVAL,
    ):
        self.chunk_size = chunk_size
        self.partial_decode_interval = partial_decode_interval
        self.stop_event = threading.Event()
        self.ready_event = threading.Event()
        self.done_event = threading.Event()
        self.data_event = threading.Event()

        self._buffers: Dict[str, Deque[bytes]] = {
            "mic": deque(),
            "system": deque(),
        }
        self._devices: Dict[str, _AudioDeviceSpec] = {}
        self._streams = {}
        self._thread: Optional[threading.Thread] = None
        self._error: Optional[str] = None
        self._lock = threading.Lock()
        self._recognizer = recognizer
        self._asr_stream = None
        self._on_partial = on_partial
        self._last_partial_text = ""
        self._final_text = ""
        self._last_decode_at = 0.0

    @property
    def error(self) -> Optional[str]:
        return self._error

    @property
    def final_text(self) -> str:
        return self._final_text or self._last_partial_text

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        if not self.ready_event.wait(timeout=4):
            self.stop_event.set()
            raise RuntimeError("语音录音初始化超时")
        if self._error:
            raise RuntimeError(self._error)

    def stop_and_transcribe(self) -> str:
        self.stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)
        if self._error:
            raise RuntimeError(self._error)
        return self.final_text.strip()

    def cancel(self):
        self.stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    def _run(self):
        pa = None
        try:
            if pyaudio is None:
                raise RuntimeError("未安装 pyaudiowpatch，无法进行语音录音")

            pa = pyaudio.PyAudio()
            mic_device = self._resolve_microphone_device(pa)
            system_device = self._resolve_system_device(pa)

            self._devices = {"mic": mic_device, "system": system_device}
            self._asr_stream = self._recognizer.create_stream()
            self._streams = {
                "mic": self._open_stream(pa, mic_device, "mic"),
                "system": self._open_stream(pa, system_device, "system"),
            }

            self.ready_event.set()
            self._run_decode_loop()
        except Exception as e:
            self._error = str(e)
            logger.error(f"语音录音启动失败: {e}")
            self.ready_event.set()
        finally:
            for stream in self._streams.values():
                with suppress(Exception):
                    if stream.is_active():
                        stream.stop_stream()
                with suppress(Exception):
                    stream.close()
            self._streams.clear()
            if pa is not None:
                with suppress(Exception):
                    pa.terminate()
            self.done_event.set()
            self.stop_event.set()

    def _run_decode_loop(self):
        while not self.stop_event.is_set():
            self.data_event.wait(timeout=0.1)
            self.data_event.clear()
            if self._drain_pending_audio(final=False):
                self._maybe_decode(force=False)

        while self._drain_pending_audio(final=True):
            pass
        self._maybe_decode(force=True)
        self._final_text = self._last_partial_text

    def _resolve_microphone_device(self, pa) -> _AudioDeviceSpec:
        info = pa.get_default_input_device_info()
        return _AudioDeviceSpec(
            index=int(info["index"]),
            name=str(info["name"]),
            sample_rate=int(info.get("defaultSampleRate") or TARGET_SAMPLE_RATE),
            channels=max(1, int(info.get("maxInputChannels") or 1)),
        )

    def _resolve_system_device(self, pa) -> _AudioDeviceSpec:
        try:
            loopback = pa.get_default_wasapi_loopback()
        except Exception:
            try:
                wasapi_info = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
            except Exception as e:
                raise RuntimeError("当前系统未检测到 WASAPI，无法录制系统音频") from e

            default_output = pa.get_device_info_by_index(wasapi_info["defaultOutputDevice"])
            if default_output.get("isLoopbackDevice"):
                loopback = default_output
            else:
                loopback = None
                for device in pa.get_loopback_device_info_generator():
                    if default_output["name"] in device["name"]:
                        loopback = device
                        break
                if loopback is None:
                    raise RuntimeError("未找到默认扬声器对应的 loopback 设备")

        return _AudioDeviceSpec(
            index=int(loopback["index"]),
            name=str(loopback["name"]),
            sample_rate=int(loopback.get("defaultSampleRate") or TARGET_SAMPLE_RATE),
            channels=max(1, int(loopback.get("maxInputChannels") or 1)),
        )

    def _open_stream(self, pa, device: _AudioDeviceSpec, key: str):
        def callback(in_data, frame_count, time_info, status):
            if in_data:
                with self._lock:
                    self._buffers[key].append(bytes(in_data))
                self.data_event.set()
            if self.stop_event.is_set():
                return (None, pyaudio.paComplete)
            return (None, pyaudio.paContinue)

        return pa.open(
            format=pyaudio.paInt16,
            channels=device.channels,
            rate=device.sample_rate,
            input=True,
            input_device_index=device.index,
            frames_per_buffer=self.chunk_size,
            stream_callback=callback,
        )

    def _drain_pending_audio(self, final: bool) -> bool:
        fed_any = False

        while True:
            pair = self._pop_next_pair(final=final)
            if pair is None:
                break

            mic_raw, system_raw = pair
            mic_audio = self._prepare_audio_chunk("mic", mic_raw)
            system_audio = self._prepare_audio_chunk("system", system_raw)
            mixed = self._mix_tracks(system_audio, mic_audio)
            if mixed.size == 0:
                continue

            self._asr_stream.accept_waveform(TARGET_SAMPLE_RATE, mixed)
            fed_any = True

        return fed_any

    def _pop_next_pair(self, final: bool) -> Optional[Tuple[bytes, bytes]]:
        with self._lock:
            mic_queue = self._buffers["mic"]
            system_queue = self._buffers["system"]

            if mic_queue and system_queue:
                return mic_queue.popleft(), system_queue.popleft()

            if not final:
                if mic_queue:
                    return mic_queue.popleft(), b""
                if system_queue:
                    return b"", system_queue.popleft()
                return None

            if mic_queue:
                return mic_queue.popleft(), b""
            if system_queue:
                return b"", system_queue.popleft()
            return None

    def _prepare_audio_chunk(self, source: str, raw_bytes: bytes) -> np.ndarray:
        if not raw_bytes:
            return np.empty(0, dtype=np.float32)

        device = self._devices.get(source)
        if device is None:
            return np.empty(0, dtype=np.float32)

        audio = np.frombuffer(raw_bytes, dtype=np.int16)
        if device.channels > 1:
            frame_count = len(audio) // device.channels
            if frame_count <= 0:
                return np.empty(0, dtype=np.float32)
            audio = audio[: frame_count * device.channels].reshape(frame_count, device.channels).mean(axis=1)

        audio = np.clip(audio.astype(np.float32) / 32768.0, -1.0, 1.0)
        return self._resample_audio(audio, device.sample_rate, TARGET_SAMPLE_RATE)

    def _resample_audio(self, audio: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
        if audio.size == 0:
            return audio.astype(np.float32, copy=False)
        if source_rate <= 0 or source_rate == target_rate or audio.size == 1:
            return audio.astype(np.float32, copy=False)

        target_length = max(1, int(round(audio.size * target_rate / source_rate)))
        source_positions = np.linspace(0.0, 1.0, num=audio.size, endpoint=False)
        target_positions = np.linspace(0.0, 1.0, num=target_length, endpoint=False)
        return np.interp(target_positions, source_positions, audio).astype(np.float32)

    def _mix_tracks(self, system_audio: np.ndarray, mic_audio: np.ndarray) -> np.ndarray:
        if system_audio.size == 0:
            return mic_audio.astype(np.float32, copy=False)
        if mic_audio.size == 0:
            return system_audio.astype(np.float32, copy=False)

        max_length = max(system_audio.size, mic_audio.size)
        if system_audio.size < max_length:
            system_audio = np.pad(system_audio, (0, max_length - system_audio.size))
        if mic_audio.size < max_length:
            mic_audio = np.pad(mic_audio, (0, max_length - mic_audio.size))

        mixed = (system_audio + mic_audio) / 2.0
        return np.clip(mixed.astype(np.float32, copy=False), -1.0, 1.0)

    def _maybe_decode(self, force: bool):
        now = time.monotonic()
        if not force and now - self._last_decode_at < self.partial_decode_interval:
            return

        if self._asr_stream is None:
            return

        self._recognizer.decode_stream(self._asr_stream)
        self._last_decode_at = now

        text = self._normalize_transcript(self._extract_transcript(self._asr_stream.result))
        if text and text != self._last_partial_text:
            self._last_partial_text = text
            if self._on_partial is not None:
                self._on_partial(text)

    def _extract_transcript(self, result) -> str:
        if result is None:
            return ""

        text = getattr(result, "text", None)
        if text:
            return str(text).strip()

        raw = str(result).strip()
        if not raw:
            return ""

        if raw.startswith("{") and raw.endswith("}"):
            with suppress(Exception):
                data = json.loads(raw)
                if isinstance(data, dict):
                    value = data.get("text", "")
                    if value:
                        return str(value).strip()

        for marker in ("text=", 'text:"', "text:"):
            if marker in raw:
                tail = raw.split(marker, 1)[1].strip()
                tail = tail.lstrip(' "\'')
                tail = tail.rstrip(' "\'}')
                if tail:
                    return tail

        return raw

    def _normalize_transcript(self, text: str) -> str:
        return " ".join(text.replace("\r", " ").replace("\n", " ").split()).strip()


class SpeechService:
    def __init__(self):
        self._lock = threading.Lock()
        self._recognizer_load_lock = threading.Lock()
        self._session: Optional[_RecordingSession] = None
        self._recognizer_cache: Dict[str, object] = {}
        self._defer_preload_until_record = False

    def is_recording(self) -> bool:
        with self._lock:
            return self._session is not None

    def start_recording(self, on_partial: Optional[Callable[[str], None]] = None) -> Tuple[bool, str]:
        with self._lock:
            if self._session is not None:
                return False, "语音录音已经在进行中"
            self._defer_preload_until_record = False

        try:
            recognizer = self._load_recognizer(allow_retry_on_bad_alloc=True)
            session = _RecordingSession(recognizer, on_partial=on_partial)
            with self._lock:
                self._session = session

            session.start()
            logger.info("语音录音已开始")
            return True, "语音录音已开始"
        except Exception as e:
            with self._lock:
                self._session = None
            logger.error(f"语音录音启动失败: {e}")
            return False, f"语音录音启动失败: {e}"

    def cancel_recording(self):
        session = self._detach_session()
        if session is not None:
            session.cancel()

    def stop_and_transcribe(self) -> Tuple[bool, str]:
        session = self._detach_session()
        if session is None:
            return False, "当前没有正在进行的录音"

        try:
            text = session.stop_and_transcribe()
        except Exception as e:
            return False, f"停止录音失败: {e}"

        if not text:
            return False, "没有识别到有效语音内容"

        logger.info(f"语音识别完成，文本长度: {len(text)}")
        return True, text

    def preload(self):
        with self._lock:
            if self._defer_preload_until_record:
                return

        try:
            self._load_recognizer(allow_retry_on_bad_alloc=False)
        except Exception as e:
            message = str(e)
            if self._is_bad_allocation(message):
                with self._lock:
                    self._defer_preload_until_record = True
                logger.warning("语音模型预加载内存不足，改为首次录音时按需加载")
                return
            logger.warning(f"语音识别模型预加载失败: {e}")

    def invalidate_cache(self):
        with self._lock:
            self._recognizer_cache.clear()
            self._defer_preload_until_record = False

    def has_model(self) -> bool:
        return self._resolve_model_root() is not None

    def _detach_session(self) -> Optional[_RecordingSession]:
        with self._lock:
            session = self._session
            self._session = None
        return session

    def _load_recognizer(self, allow_retry_on_bad_alloc: bool = True):
        if sherpa_onnx is None:
            raise RuntimeError("未安装 sherpa-onnx，无法进行离线语音识别")

        model_root = self._resolve_model_root()
        if model_root is None:
            raise RuntimeError("未找到语音模型目录，请在设置中配置包含 model.onnx 与 tokens.txt 的目录")

        cache_key = str(model_root.resolve())
        with self._lock:
            cached = self._recognizer_cache.get(cache_key)
            if cached is not None:
                return cached

        with self._recognizer_load_lock:
            with self._lock:
                cached = self._recognizer_cache.get(cache_key)
                if cached is not None:
                    return cached

            model_path = model_root / "model.onnx"
            tokens_path = model_root / "tokens.txt"
            if not model_path.is_file() or not tokens_path.is_file():
                raise RuntimeError(f"语音模型目录无效: {model_root}")

            logger.info(f"加载语音识别模型: {model_root}")
            recognizer = self._create_recognizer_with_fallback(
                model_path=model_path,
                tokens_path=tokens_path,
                allow_retry_on_bad_alloc=allow_retry_on_bad_alloc,
            )

            with self._lock:
                self._recognizer_cache[cache_key] = recognizer

            return recognizer

    def _create_recognizer_with_fallback(
        self,
        model_path: Path,
        tokens_path: Path,
        allow_retry_on_bad_alloc: bool,
    ):
        attempts = [True, False] if allow_retry_on_bad_alloc else [True]
        last_error: Optional[Exception] = None

        for idx, use_itn in enumerate(attempts, start=1):
            try:
                return sherpa_onnx.OfflineRecognizer.from_sense_voice(
                    model=str(model_path),
                    tokens=str(tokens_path),
                    num_threads=1,
                    provider="cpu",
                    use_itn=use_itn,
                    debug=False,
                )
            except Exception as e:
                last_error = e
                if not self._is_bad_allocation(str(e)):
                    continue
                if idx >= len(attempts):
                    continue
                logger.warning("语音模型加载出现内存压力，正在使用轻量参数重试...")
                gc.collect()
                time.sleep(0.2)

        raise last_error or RuntimeError("语音模型加载失败")

    def _is_bad_allocation(self, message: str) -> bool:
        normalized = (message or "").lower()
        return "bad allocation" in normalized or "std::bad_alloc" in normalized

    def _resolve_model_root(self) -> Optional[Path]:
        configured_dir = getattr(config_manager.config, "speech_model_dir", "").strip()
        candidate_roots = []

        if configured_dir:
            candidate_roots.append(Path(configured_dir).expanduser())

        project_assets = get_project_root() / "assets" / "speech_models"
        user_assets = get_user_data_dir() / "speech_models"
        candidate_roots.extend(
            [
                project_assets,
                project_assets / DEFAULT_MODEL_FOLDER,
                user_assets,
                user_assets / DEFAULT_MODEL_FOLDER,
            ]
        )

        for root in candidate_roots:
            resolved = self._find_bundle_root(root)
            if resolved is not None:
                return resolved

        return None

    def _find_bundle_root(self, root: Path) -> Optional[Path]:
        if not root.exists():
            return None

        if root.is_file():
            root = root.parent

        if self._is_bundle_root(root):
            return root

        if root.is_dir():
            for child in root.iterdir():
                if child.is_dir() and self._is_bundle_root(child):
                    return child

        return None

    def _is_bundle_root(self, root: Path) -> bool:
        return (root / "model.onnx").is_file() and (root / "tokens.txt").is_file()


speech_service = SpeechService()
