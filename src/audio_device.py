"""録音ごとに作る private 集約デバイス。CoreAudio は作成時だけ読み込む。"""
import ctypes as C
from dataclasses import dataclass
import plistlib
import time
import uuid


def select_microphone(devices, usb_name, builtin_name):
    """名前と入力ch数の一覧から (デバイス, 表示名) を選ぶ純粋関数。"""
    for name, kind in ((usb_name, "USB"), (builtin_name, "内蔵")):
        for device in devices:
            if device["name"] == name and device["max_input_channels"] > 0:
                return device, kind
    raise RuntimeError("使用できるマイクがありません")


def _fourcc(value):
    return int.from_bytes(value.encode("ascii"), "big")


class _Address(C.Structure):
    _fields_ = [("selector", C.c_uint32), ("scope", C.c_uint32), ("element", C.c_uint32)]


class CoreAudio:
    def __init__(self):
        self.ca = C.CDLL("/System/Library/Frameworks/CoreAudio.framework/CoreAudio")
        self.cf = C.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        for name, args, result in (
            ("AudioObjectGetPropertyDataSize", [C.c_uint32, C.POINTER(_Address), C.c_uint32, C.c_void_p, C.POINTER(C.c_uint32)], C.c_int32),
            ("AudioObjectGetPropertyData", [C.c_uint32, C.POINTER(_Address), C.c_uint32, C.c_void_p, C.POINTER(C.c_uint32), C.c_void_p], C.c_int32),
            ("AudioHardwareCreateAggregateDevice", [C.c_void_p, C.POINTER(C.c_uint32)], C.c_int32),
            ("AudioHardwareDestroyAggregateDevice", [C.c_uint32], C.c_int32),
        ):
            fn = getattr(self.ca, name)
            fn.argtypes, fn.restype = args, result
        for name, args, result in (
            ("CFDataCreate", [C.c_void_p, C.c_void_p, C.c_long], C.c_void_p),
            ("CFPropertyListCreateWithData", [C.c_void_p, C.c_void_p, C.c_ulong, C.c_void_p, C.c_void_p], C.c_void_p),
            ("CFStringGetLength", [C.c_void_p], C.c_long),
            ("CFStringGetMaximumSizeForEncoding", [C.c_long, C.c_uint32], C.c_long),
            ("CFStringGetCString", [C.c_void_p, C.c_void_p, C.c_long, C.c_uint32], C.c_bool),
            ("CFRelease", [C.c_void_p], None),
        ):
            fn = getattr(self.cf, name)
            fn.argtypes, fn.restype = args, result

    @staticmethod
    def check(status):
        if status:
            raise RuntimeError(f"CoreAudio OSStatus={status}")

    def property(self, device, selector):
        address = _Address(_fourcc(selector), _fourcc("glob"), 0)
        size = C.c_uint32()
        self.check(self.ca.AudioObjectGetPropertyDataSize(device, C.byref(address), 0, None, C.byref(size)))
        data = C.create_string_buffer(size.value)
        self.check(self.ca.AudioObjectGetPropertyData(device, C.byref(address), 0, None, C.byref(size), data))
        return data.raw[:size.value]

    def string_property(self, device, selector):
        ref = C.c_void_p.from_buffer_copy(self.property(device, selector))
        try:
            size = self.cf.CFStringGetMaximumSizeForEncoding(self.cf.CFStringGetLength(ref), 0x08000100) + 1
            buffer = C.create_string_buffer(size)
            if not self.cf.CFStringGetCString(ref, buffer, size, 0x08000100):
                raise RuntimeError("CoreAudio の文字列を読めません")
            return buffer.value.decode("utf-8")
        finally:
            self.cf.CFRelease(ref)

    def uid_for_name(self, name):
        raw = self.property(1, "dev#")
        for device in (C.c_uint32 * (len(raw) // 4)).from_buffer_copy(raw):
            if self.string_property(device, "lnam") == name:
                return self.string_property(device, "uid ")
        raise RuntimeError(f"デバイスが見つかりません: {name}")

    def create(self, description):
        payload = plistlib.dumps(description)
        data = self.cf.CFDataCreate(None, payload, len(payload))
        if not data:
            raise RuntimeError("CFDataCreate に失敗しました")
        try:
            ref = self.cf.CFPropertyListCreateWithData(None, data, 0, None, None)
            if not ref:
                raise RuntimeError("集約デバイス設定を作成できません")
            try:
                device = C.c_uint32()
                self.check(self.ca.AudioHardwareCreateAggregateDevice(ref, C.byref(device)))
                return device.value
            finally:
                self.cf.CFRelease(ref)
        finally:
            self.cf.CFRelease(data)

    def destroy(self, device):
        self.check(self.ca.AudioHardwareDestroyAggregateDevice(device))


@dataclass
class AggregateDevice:
    backend: CoreAudio
    device_id: int
    name: str
    channels: int
    mic_kind: str
    input_index: int | None = None

    def destroy(self):
        if self.device_id:
            self.backend.destroy(self.device_id)
            self.device_id = 0


def refresh_devices(sd):
    # PortAudio が保持している起動時のデバイス一覧を更新する。
    sd._terminate()
    sd._initialize()


def create_aggregate_device(sd, usb_name, builtin_name, blackhole_name):
    refresh_devices(sd)
    devices = sd.query_devices()
    mic, kind = select_microphone(devices, usb_name, builtin_name)
    if not any(d["name"] == blackhole_name and d["max_input_channels"] == 2 for d in devices):
        raise RuntimeError(f"{blackhole_name} がありません")
    backend = CoreAudio()
    mic_uid = backend.uid_for_name(mic["name"])
    blackhole_uid = backend.uid_for_name(blackhole_name)
    uid = f"mojiokoshi.{uuid.uuid4()}"
    name = uid
    description = {
        "name": name, "uid": uid, "private": 1,
        "master": mic_uid,
        "subdevices": [{"uid": mic_uid}, {"uid": blackhole_uid, "drift": 1}],
    }
    device = AggregateDevice(backend, backend.create(description), name,
                             int(mic["max_input_channels"]) + 2, kind)
    try:
        # HAL から PortAudio への反映を短時間待つ。作成途中のch数も除外する。
        for _ in range(10):
            refresh_devices(sd)
            for index, info in enumerate(sd.query_devices()):
                if info["name"] == name and info["max_input_channels"] == device.channels:
                    device.input_index = index
                    return device
            time.sleep(0.05)
        raise RuntimeError("作成した集約デバイスを PortAudio で確認できません")
    except Exception:
        device.destroy()
        time.sleep(0.1)  # 消した直後に読み直すと PortAudio が消えたデバイスを見て警告を出す
        refresh_devices(sd)
        raise
