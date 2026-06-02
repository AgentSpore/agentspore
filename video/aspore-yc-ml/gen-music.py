#!/usr/bin/env python3
"""29s synthwave: saw lead + 4-on-floor kick + snare + arp."""
import numpy as np
from scipy.io import wavfile
import subprocess
from pathlib import Path

SR = 44100; DUR = 29.0
N = int(SR * DUR); t = np.linspace(0, DUR, N, endpoint=False)
NOTES = {"A1":55,"E2":82.41,"A2":110,"C3":130.81,"E3":164.81,"A3":220,"C4":261.63,"E4":329.63,"A4":440,"C5":523.25,"E5":659.25,"A5":880,"C6":1046.5,"E6":1318.5}

def env(start, dur, a=0.02, r=0.3):
    e = np.zeros(N)
    i0 = int(start*SR); i1 = min(int((start+dur)*SR), N)
    if i0 >= N: return e
    n = i1 - i0; ai = max(1, int(a*SR)); ri = max(1, int(r*SR))
    seg = np.ones(n)
    if ai < n: seg[:ai] = np.linspace(0,1,ai)
    if ri < n: seg[-ri:] = np.linspace(1,0,ri)
    e[i0:i1] = seg
    return e

def saw(f, st, du, amp=0.20):
    """Detuned saw lead — 3 sawtooth osc slightly detuned."""
    e = env(st, du, 0.05, 0.4)
    s = np.zeros(N)
    for det in (1.0, 1.007, 0.993):
        ph = 2*np.pi*f*det*t
        s += 2*(ph/(2*np.pi) - np.floor(0.5 + ph/(2*np.pi)))
    # lowpass via moving avg
    k = 8
    s = np.convolve(s, np.ones(k)/k, mode='same')
    return s * e * amp / 3

def pad(f, st, du, amp=0.16):
    e = env(st, du, 0.6, 1.2)
    return (np.sin(2*np.pi*f*t) + 0.5*np.sin(2*np.pi*f*2*t) + 0.3*np.sin(2*np.pi*f*1.005*t)) * e * amp

def kick(st, amp=0.65):
    e = np.zeros(N)
    i0 = int(st*SR); n = int(0.35*SR); i1 = min(i0+n, N)
    if i0 >= N: return e
    st_t = np.linspace(0, 0.35, i1-i0)
    pitch = 95 * np.exp(-st_t*14) + 42
    body = np.sin(2*np.pi*np.cumsum(pitch)/SR)
    e[i0:i1] = body * np.exp(-st_t*6) * amp
    cn = min(int(0.005*SR), i1-i0)
    e[i0:i0+cn] += np.random.uniform(-1,1,cn) * 0.30
    return e

def snare(st, amp=0.28):
    e = np.zeros(N)
    i0 = int(st*SR); n = int(0.18*SR); i1 = min(i0+n, N)
    if i0 >= N: return e
    st_t = np.linspace(0, 0.18, i1-i0)
    body = np.sin(2*np.pi*180*t[i0:i1]) * 0.3
    noise = np.random.normal(0, 1, i1-i0) * 0.7
    e[i0:i1] = (body + noise) * np.exp(-st_t*16) * amp
    return e

def hat(st, amp=0.10):
    e = np.zeros(N)
    i0 = int(st*SR); n = int(0.05*SR); i1 = min(i0+n, N)
    if i0 >= N: return e
    st_t = np.linspace(0, 0.05, i1-i0)
    noise = np.random.normal(0, 1, i1-i0)
    # highpass: subtract smoothed
    k = 4
    sm = np.convolve(noise, np.ones(k)/k, mode='same')
    e[i0:i1] = (noise - sm) * np.exp(-st_t*40) * amp
    return e

def arp(f, st, du=0.2):
    e = env(st, du, 0.005, 0.18)
    return (np.sin(2*np.pi*f*t) + 0.3*np.sin(2*np.pi*f*2.01*t)) * e * 0.09

audio = np.zeros(N)

# Pad chord progression Am-F-C-G (8 bars × ~3.6s)
chords = [
    ("A2","C3","E3","A3"),  # Am
    ("A2","C3","E3","A3"),
    ("E2","A2","C3","E3"),  # Am inv (F-like) - softer
    ("E2","A2","C3","E3"),
    ("C3","E3","A3","C4"),  # C
    ("C3","E3","A3","C4"),
    ("E2","A2","C3","E3"),  # back to Am
    ("E2","A2","C3","E3"),
]
seg = DUR / len(chords)
for i, ch in enumerate(chords):
    for note in ch: audio += pad(NOTES[note], i*seg, seg+0.5)

# Sub bass A1 drone
for i in range(int(DUR // 4)):
    audio += pad(NOTES["A1"], i*4, 4.5, amp=0.55) * 0.6

# 4-on-floor kick — every 0.5s (120 BPM) starting 2s, ending 27s
for kt in np.arange(2.0, 27.5, 0.5):
    audio += kick(kt, amp=0.85)

# Snare on 2 and 4 (offset 1.0s from kick start)
for st in np.arange(3.0, 27.0, 1.0):
    audio += snare(st)

# Hi-hat 8th notes from 6s
for ht in np.arange(6.0, 26.0, 0.25):
    audio += hat(ht)

# Saw lead — main melody A minor pentatonic, starting 10s for 14s
# Pattern: A4 C5 E5 C5 A4 E5 A5 E5 (one bar each 2s)
lead_notes = [
    ("A4", 10.0, 1.5),
    ("C5", 11.5, 1.0),
    ("E5", 12.5, 2.0),
    ("C5", 14.5, 1.0),
    ("A4", 15.5, 1.0),
    ("E5", 16.5, 1.5),
    ("A5", 18.0, 2.5),
    ("E5", 20.5, 1.5),
    ("C5", 22.0, 1.5),
    ("A4", 23.5, 2.0),
]
for note, st, du in lead_notes:
    audio += saw(NOTES[note], st, du, amp=0.18)

# Arp sparkles on top — 16ths from 4s
arp_notes = ["A5","C6","E6","C6","A5","E6","C6","A5"]
for i in range(int((DUR-6)/0.25)):
    note = arp_notes[i % len(arp_notes)]
    audio += arp(NOTES[note], 4.0 + i*0.25, 0.18) * 0.7

# Final chime
for note in ["E6","A5","C6"]:
    audio += arp(NOTES[note], 26.0, 2.5) * 1.8

fi = int(1.0*SR); fo = int(1.5*SR)
audio[:fi] *= np.linspace(0,1,fi)
audio[-fo:] *= np.linspace(1,0,fo)
audio = audio / np.max(np.abs(audio)) * 0.85

out_dir = Path(__file__).parent / "assets"; out_dir.mkdir(exist_ok=True)
wav = out_dir / "_tmp.wav"; mp3 = out_dir / "bg-music.mp3"
wavfile.write(wav, SR, (audio * 32767).astype(np.int16))
subprocess.run(["ffmpeg","-y","-i",str(wav),"-codec:a","libmp3lame","-b:a","192k",str(mp3)], check=True, capture_output=True)
wav.unlink()
print(f"wrote {mp3}")
