"""Bounded-memory audio alignment checks against a continuous timeline mix.

Decode PCM in four-second blocks. Correlation is evidence of timing, not a
claim about subjective audio quality. A lag only fails when it substantially
explains a mismatch better than the expected zero-offset alignment.
"""
from __future__ import annotations

import subprocess
import numpy as np


class AudioIntegrityError(RuntimeError):
    def __init__(self, message, report):
        super().__init__(message)
        self.report = report


def alignment(reference, measured, rate=8000):
    reference = reference.astype(np.float64)
    measured = measured.astype(np.float64)
    reference -= reference.mean(); measured -= measured.mean()
    reference_rms, measured_rms = float(np.sqrt(np.mean(reference ** 2))), float(np.sqrt(np.mean(measured ** 2)))
    if max(reference_rms, measured_rms) < 1e-4:
        return None
    if ((reference_rms > .001 and measured_rms < reference_rms * .01) or
            (reference_rms < 1e-4 and measured_rms > .01)):
        return {"zeroOffsetCorrelation": 0., "bestCorrelation": 0., "estimatedOffsetSeconds": 0.,
                "gain": measured_rms / max(reference_rms, 1e-12), "shifted": True,
                "problem": "missing-audio" if reference_rms > measured_rms else "unexpected-audio"}
    energy = float(np.dot(reference, reference) * np.dot(measured, measured))
    if energy < 1e-14:
        return None
    zero = float(np.dot(reference, measured) / np.sqrt(energy))
    count = len(reference)
    fft_size = 1 << (2 * count - 1).bit_length()
    dots = np.fft.irfft(np.fft.rfft(reference, fft_size) *
                        np.conj(np.fft.rfft(measured, fft_size)), fft_size)
    bound = min(rate // 20, count // 4)
    lags = np.arange(-bound, bound + 1)
    r_energy = np.r_[0., np.cumsum(reference * reference)]
    m_energy = np.r_[0., np.cumsum(measured * measured)]
    r_start, r_end = np.maximum(lags, 0), count + np.minimum(lags, 0)
    m_start, m_end = np.maximum(-lags, 0), count - np.maximum(lags, 0)
    denominator = np.sqrt(np.maximum((r_energy[r_end] - r_energy[r_start]) *
                                    (m_energy[m_end] - m_energy[m_start]), 1e-20))
    scores = dots[lags % fft_size] / denominator
    best = int(np.argmax(scores)); offset = int(lags[best])
    improvement = float(scores[best] - zero)
    shifted = abs(offset) > 1 and scores[best] > .8 and improvement > .08
    return {"zeroOffsetCorrelation": zero, "bestCorrelation": float(scores[best]),
            "estimatedOffsetSeconds": offset / rate if improvement > .08 else 0.,
            "gain": float(np.dot(reference, measured) / max(np.dot(reference, reference), 1e-20)),
            "shifted": bool(shifted)}


def verify_audio(reference_path, output_path, ffmpeg="ffmpeg", cancel_event=None):
    rate, channels, block_frames = 8000, 2, 32000
    processes = []
    report = {"status": "passed", "comparison": "continuous-timeline-pcm",
              "sampleRate": rate, "blockSeconds": block_frames / rate, "windows": []}
    def read_block(stream, amount):
        chunks = []; remaining = amount
        while remaining:
            chunk = stream.read(remaining)
            if not chunk: break
            chunks.append(chunk); remaining -= len(chunk)
        return b"".join(chunks)
    counts = [0, 0]
    try:
        for path in (reference_path, output_path):
            processes.append(subprocess.Popen([ffmpeg, '-nostdin', '-v', 'error', '-i', str(path),
                '-map', '0:a:0', '-ac', str(channels), '-ar', str(rate), '-f', 'f32le', 'pipe:1'],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE))
        while True:
            if cancel_event and cancel_event.is_set():
                raise InterruptedError("audio verification cancelled")
            blocks = [read_block(process.stdout, block_frames * channels * 4) for process in processes]
            if not any(blocks): break
            arrays = [np.frombuffer(block, np.float32).reshape(-1, channels) for block in blocks]
            before = counts[0]
            for i, values in enumerate(arrays): counts[i] += len(values)
            count = min(map(len, arrays))
            if count < rate // 4: continue
            # Avoid stereo cancellation: inspect the strongest reference channel.
            channel = int(np.argmax(np.sum(arrays[0][:count] ** 2, axis=0)))
            result = alignment(arrays[0][:count, channel], arrays[1][:count, channel], rate)
            if result is not None:
                report['windows'].append({"atSeconds": before / rate, **result})
                if result['shifted']: report['status'] = 'failed'
        for process in processes:
            error = process.stderr.read(); code = process.wait()
            if code:
                report['status'] = 'failed'
                raise AudioIntegrityError('Audio verification decode failed: ' + error.decode('utf-8', errors='replace')[-600:], report)
        report['lengthDifferenceSeconds'] = (counts[1] - counts[0]) / rate
        report['checkedSeconds'] = counts[0] / rate
        if abs(report['lengthDifferenceSeconds']) > .05: report['status'] = 'failed'
        if report['status'] != 'passed':
            first = next((item for item in report['windows'] if item['shifted']), None)
            detail = (f"at {first['atSeconds']:.3f}s, {first.get('problem', 'shifted audio')}, offset {first['estimatedOffsetSeconds']:.6f}s"
                      if first else f"length differs by {report['lengthDifferenceSeconds']:.6f}s")
            raise AudioIntegrityError('Audio alignment verification failed: ' + detail, report)
        return report
    finally:
        for process in processes:
            if process.poll() is None: process.kill()
            process.wait()
            process.stdout.close(); process.stderr.close()
