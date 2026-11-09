"""マイク選択と録音ごとの集約デバイス管理。実機 CoreAudio は呼ばない。"""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import audio_device as a

USB = 'UGREEN USB MIC-CM769'
BUILTIN = 'MacBook Airのマイク'


def info(name, channels):
    return {'name': name, 'max_input_channels': channels}


@pytest.mark.parametrize('devices,kind,channels', [
    ([info(BUILTIN, 1), info(USB, 2)], 'USB', 2),
    ([info(BUILTIN, 1)], '内蔵', 1),
    ([info(USB, 0), info(BUILTIN, 1)], '内蔵', 1),
])
def test_select(devices, kind, channels):
    device, selected_kind = a.select_microphone(devices, USB, BUILTIN)
    assert selected_kind == kind
    assert device['max_input_channels'] == channels


def test_no_microphone():
    with pytest.raises(RuntimeError):
        a.select_microphone([info('other', 2)], USB, BUILTIN)


@pytest.fixture
def recorder(tmp_path, monkeypatch):
    # 既存テストの偽 sounddevice / モデルを再利用する。
    import test_mojiokoshi as legacy
    m = legacy.mojiokoshi
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(m, 'create_aggregate_device', Mock(side_effect=RuntimeError('no BlackHole')))
    instance = m.MojiOkoshi()
    assert instance.model_ready.wait(5)
    yield instance, m
    if instance.is_running or instance.is_paused:
        instance.stop()


def test_fallback(recorder, capsys):
    instance, m = recorder
    instance.start()
    assert m.sd.default.device == m.SD_DEVICE
    assert m.sd.default.channels == m.detect_input_channels()
    assert instance.mic_kind is None
    assert '従来の mojiokoshi' in capsys.readouterr().out


@pytest.mark.parametrize('kind,channels', [('USB', 4), ('内蔵', 3)])
def test_lifecycle(recorder, kind, channels):
    instance, m = recorder
    events = []
    devices = [SimpleNamespace(input_index=12, channels=channels, mic_kind=kind,
                               destroy=Mock(side_effect=lambda: events.append('destroy')))
               for _ in range(2)]
    m.create_aggregate_device.side_effect = devices
    instance.start()
    assert instance.mic_kind == kind
    assert m.sd.default.channels == channels
    instance.stream.close = lambda: events.append('close')
    instance.pause()
    assert events == ['close', 'destroy']
    assert instance.mic_kind is None
    devices[0].destroy.assert_called_once()
    instance.resume()
    assert instance.mic_kind == kind
    instance.stop()
    devices[1].destroy.assert_called_once()
    assert m.create_aggregate_device.call_count == 2


def test_stream_failure_destroys(recorder, monkeypatch):
    instance, m = recorder
    device = SimpleNamespace(input_index=12, channels=3, mic_kind='内蔵', destroy=Mock())
    m.create_aggregate_device.side_effect = None
    m.create_aggregate_device.return_value = device
    monkeypatch.setattr(m.sd, 'InputStream', Mock(side_effect=RuntimeError('stream failed')))
    with pytest.raises(RuntimeError):
        instance.start()
    device.destroy.assert_called_once()
    assert instance.mic_kind is None


def test_composition_and_refresh():
    sd = SimpleNamespace(_terminate=Mock(), _initialize=Mock())
    physical = [info(USB, 2), info('BlackHole 2ch', 2)]
    backend = Mock()
    backend.uid_for_name.side_effect = ['mic-uid', 'bh-uid']
    backend.create.return_value = 42
    def devices():
        if backend.create.called:
            description = backend.create.call_args.args[0]
            return physical + [info(description['name'], 4)]
        return physical
    sd.query_devices = devices
    with patch.object(a, 'CoreAudio', return_value=backend):
        device = a.create_aggregate_device(sd, USB, BUILTIN, 'BlackHole 2ch')
    description = backend.create.call_args.args[0]
    assert description['private'] == 1
    assert description['master'] == 'mic-uid'
    assert description['subdevices'] == [{'uid': 'mic-uid'}, {'uid': 'bh-uid', 'drift': 1}]
    assert device.input_index == 2
    assert sd._initialize.call_count == 2
    device.destroy()
    device.destroy()
    backend.destroy.assert_called_once_with(42)
