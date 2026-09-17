"""Download pinned public models. No recordings are uploaded."""
from pathlib import Path
import urllib.request
import zipfile
root=Path(__file__).resolve().parents[1]
models=root/'.tbot-data/models';models.mkdir(parents=True,exist_ok=True)
voice=models/'vosk-model-small-en-us-0.15'
if not voice.exists():
    archive=models/'voice.zip'
    if not archive.exists() or not zipfile.is_zipfile(archive):
        urllib.request.urlretrieve('https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip',archive)
    with zipfile.ZipFile(archive) as z:
        for item in z.infolist():
            if not (models/item.filename).resolve().is_relative_to(models.resolve()):raise ValueError('Unsafe archive path')
        z.extractall(models)
    archive.unlink()
hand=root/'public/models/hand_landmarker.task';hand.parent.mkdir(parents=True,exist_ok=True)
if not hand.exists():urllib.request.urlretrieve('https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task',hand)
print('Offline voice and hand models ready.')
