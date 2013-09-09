# -*- coding: utf-8 -*-
# Copyright 2013 Andrew Morgan <ziltro@ziltro.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import time
import os
import struct
import sys

from chirp import chirp_common, directory, memmap
from chirp import bitwise
from chirp.settings import RadioSetting, RadioSettingGroup, \
    RadioSettingValueInteger, RadioSettingValueList, \
    RadioSettingValueBoolean

DEBUG = os.getenv("CHIRP_DEBUG") and True or False

MEM_FORMAT = """
#seekto 0x0010;
struct {
    lbcd rxfreq[4];
    lbcd txfreq[4];
    lbcd rxtone[2];
    lbcd txtone[2];
    u8 unknown3:1,
       unknown2:1,
       unknown1:1,
       scanadd:1,
       lowpower:1,
       wide:1,
       beatshift:1,
       bcl:1;
    u8 unknown4[3];
} memory[16];
#seekto 0x02B0;
struct {
    u8 voiceprompt;
    u8 voicelanguage;
    u8 scan;
    u8 vox;
    u8 voxlevel;
    u8 voxinhibitonrx;
    u8 lowvolinhibittx;
    u8 highvolinhibittx;
    u8 alarm;
    u8 fmradio;
} settings;
#seekto 0x03C0;
struct {
    u8 beep:1,
       batterysaver:1,
       unused:6;
    u8 squelchlevel;
    u8 sidekeyfunction;
    u8 timeouttimer;
} settings2;
"""

CMD_ACK = "\x06"
BLOCK_SIZE = 0x08
UPLOAD_BLOCKS = [range(0x0000, 0x0110, 8),
                 range(0x02b0, 0x02c0, 8),
                 range(0x0380, 0x03e0, 8)]

# TODO: Is it 1 watt?
H777_POWER_LEVELS = [chirp_common.PowerLevel("High", watts=5.00),
                     chirp_common.PowerLevel("Low", watts=1.00)]
VOICE_LIST = ["English", "Chinese"]
SIDEKEYFUNCTION_LIST = ["Off", "Monitor", "Transmit Power", "Alarm"]
TIMEOUTTIMER_LIST = ["Off", "30 seconds", "60 seconds", "90 seconds",
                     "120 seconds", "150 seconds", "180 seconds",
                     "210 seconds", "240 seconds", "270 seconds",
                     "300 seconds"]

SETTING_LISTS = {
    "voice" : VOICE_LIST,
    }

def debug_print_hex(hexstr):
    for a in range(0, len(hexstr)):
        sys.stdout.write("%02x " % (ord(hexstr[a])))

def _h777_enter_programming_mode(radio):
    serial = radio.pipe

    serial.write("\x02")
    time.sleep(0.1)
    serial.write("PROGRAM")
    if serial.read(1) != CMD_ACK:
        raise Exception("Didn't get a response from the radio. "
                        "Is it turned on and plugged in firmly?")

    serial.write("\x02")
    ident = serial.read(8)
    if not ident.startswith("P3107"):
        raise Exception("Invalid response. "
                        "Is this really the correct model of radio?")

    serial.write(CMD_ACK)
    if serial.read(1) != CMD_ACK:
        raise Exception("Invalid response. "
                        "Is this really the correct model of radio?")

def _h777_exit_programming_mode(radio):
    serial = radio.pipe
    serial.write("E")

def _h777_read_block(radio, block_addr, block_size):
    serial = radio.pipe

    cmd = struct.pack(">cHb", 'R', block_addr, BLOCK_SIZE)
    expectedresponse = "W" + cmd[1:]
    if DEBUG:
        print("Reading block %04x..." % (block_addr))

    serial.write(cmd)
    response = serial.read(4 + BLOCK_SIZE)
    if response[:4] != expectedresponse:
        raise Exception("Error reading block %04x." % (block_addr))

    block_data = response[4:]

    serial.write(CMD_ACK)
    if serial.read(1) != CMD_ACK:
        raise Exception("No ACK reading block %04x." % (block_addr))

    return block_data

def _h777_write_block(radio, block_addr, block_size):
    serial = radio.pipe

    cmd = struct.pack(">cHb", 'W', block_addr, BLOCK_SIZE)
    data = radio.get_mmap()[block_addr:block_addr + 8]

    if DEBUG:
        print("Writing Data:")
        debug_print_hex(cmd + data)
        print("")

    serial.write(cmd + data)

    if serial.read(1) != CMD_ACK:
        raise Exception("No ACK")

def do_download(radio):
    print "download"
    _h777_enter_programming_mode(radio)

    data = ""

    status = chirp_common.Status()
    status.msg = "Cloning from radio"

    status.cur = 0
    status.max = radio._memsize

    for addr in range(0, radio._memsize, BLOCK_SIZE):
        status.cur = addr + BLOCK_SIZE
        radio.status_fn(status)

        block = _h777_read_block(radio, addr, BLOCK_SIZE)
        data += block

        if DEBUG:
            sys.stdout.write("%04x: " % (addr))
            debug_print_hex(block)
            print("")

    _h777_exit_programming_mode(radio)

    return memmap.MemoryMap(data)

def do_upload(radio):
    status = chirp_common.Status()
    status.msg = "Uploading to radio"

    _h777_enter_programming_mode(radio)

    status.cur = 0
    status.max = radio._memsize

    for start_addr, end_addr in radio._ranges:
        for addr in range(start_addr, end_addr, BLOCK_SIZE):
            status.cur = addr + BLOCK_SIZE
            radio.status_fn(status)
            _h777_write_block(radio, addr, BLOCK_SIZE)

    _h777_exit_programming_mode(radio)

def maybe_register(cls):
    if DEBUG:
        return directory.register(cls)
    else:
        return cls

#@directory.register
@maybe_register
class H777Radio(chirp_common.CloneModeRadio):
    """HST H-777"""
    VENDOR = "Heng Shun Tong (恒顺通)"
    MODEL = "H-777"
    BAUD_RATE = 9600

    # This code currently requires that ranges start at 0x0000
    # and are continious. In the original program 0x0388 and 0x03C8
    # are only written (all bytes 0xFF), not read.
    #_ranges = [
    #       (0x0000, 0x0110),
    #       (0x02B0, 0x02C0),
    #       (0x0380, 0x03E0)
    #       ]
    # Memory starts looping at 0x1000... But not every 0x1000.

    _ranges = [
        (0x0000, 0x0110),
        (0x02B0, 0x02C0),
        (0x0380, 0x03E0),
        ]
    _memsize = 0x03E0

    def get_features(self):
        rf = chirp_common.RadioFeatures()
        rf.has_settings = True
        rf.valid_modes = ["NFM", "FM"]  # 12.5 KHz, 25 kHz.
        rf.valid_skips = ["", "S"]
        # TODO: Support CTCSS and DCS.
        # rf.valid_tmodes = ["", "TSQL", "DTCS"]
        # rf.has_ctone = True
        # rf.has_cross = True
        # rf.has_rx_dtcs = True
        rf.valid_tmodes = [""]
        rf.has_ctone = False
        rf.has_cross = False
        rf.has_rx_dtcs = False
        rf.has_tuning_step = False
        rf.has_bank = False
        rf.has_name = False
        rf.memory_bounds = (1, 16)
        rf.valid_bands = [(400000000, 470000000)]
        rf.valid_power_levels = [chirp_common.PowerLevel("High", watts=5.00),
                                 chirp_common.PowerLevel("Low", watts=1.0)]

        return rf

    def sync_in(self):
        self._mmap = do_download(self)
        self._memobj = bitwise.parse(MEM_FORMAT, self._mmap)

    def sync_out(self):
        do_upload(self)

    def get_raw_memory(self, number):
        return repr(self._memobj.memory[number])

    def get_memory(self, number):
        _mem = self._memobj.memory[number - 1]

        mem = chirp_common.Memory()

        mem.number = number
        mem.freq = int(_mem.rxfreq) * 10

        # We'll consider any blank (i.e. 0MHz frequency) to be empty
        if mem.freq == 0:
            mem.empty = True

        if _mem.rxfreq.get_raw() == "\xFF\xFF\xFF\xFF":
            mem.freq = 0
            mem.empty = True

        # TODO: Support empty TX frequency

        if int(_mem.rxfreq) == int(_mem.txfreq):
            mem.duplex = ""
            mem.offset = 0
        else:
            mem.duplex = int(_mem.rxfreq) > int(_mem.txfreq) and "-" or "+"
            mem.offset = abs(int(_mem.rxfreq) - int(_mem.txfreq)) * 10

        mem.mode = not _mem.wide and "FM" or "NFM"
        mem.power = H777_POWER_LEVELS[not _mem.lowpower]
        # TODO: Invert lowpower flag?

        if not _mem.scanadd:
            mem.skip = "S"

        # Decode CTCSS/DCS, if used.

        if _mem.rxtone.get_raw() == "\xFF\xFF":
            mem.tmode = ""
        else:
            if ord(_mem.rxtone[1].get_raw()) & 0x80:
                # TODO: Make DCS work.
                raise Exception("Sorry, DCS isn't supported yet.")
                mem.tmode = "DTCS"
                mem.rx_dtcs = int(_mem.rxtone.get_raw() & 0x0FFF)
                mem.dtcs = int(_mem.txtone.get_raw() & 0x0FFF)

                if ord(_mem.rxtone[1].get_raw()) & 0x40:
                    mem.dtsc_polarity = "R"
                else:
                    print("DCS N")
            else:
                mem.tmode = "TSQL"
                mem.rtone = int(_mem.rxtone) / 10.0
                mem.ctone = int(_mem.txtone) / 10.0

        # TODO: Set beatshift and bcl.

        return mem

    def set_memory(self, mem):
        # Get a low-level memory object mapped to the image
        _mem = self._memobj.memory[mem.number - 1]

        if mem.empty:
            _mem.set_raw("\xFF" * (_mem.size() / 8))
            return

        _mem.rxfreq = mem.freq / 10

        if mem.duplex == "off":
            for i in range(0, 4):
                _mem.txfreq[i].set_raw("\xFF")
        elif mem.duplex == "split":
            _mem.txfreq = mem.offset / 10
        elif mem.duplex == "+":
            _mem.txfreq = (mem.freq + mem.offset) / 10
        elif mem.duplex == "-":
            _mem.txfreq = (mem.freq - mem.offset) / 10
        else:
            _mem.txfreq = mem.freq / 10

            # TODO: Support empty TX frequency

        _mem.wide = mem.mode != 0
        _mem.lowpower = mem.power == 1
        _mem.scanadd = mem.skip != "S"
        # TODO: Set beatshift and bcl.

    def get_settings(self):
        _settings = self._memobj.settings
        _settings2 = self._memobj.settings2
        basic = RadioSettingGroup("basic", "Basic Settings")

        # TODO: Check that all these settings actually do what they
        # say they do.

        rs = RadioSetting("voiceprompt", "Voice Prompt",
                          RadioSettingValueBoolean(_settings.voiceprompt))
        basic.append(rs)

        rs = RadioSetting(
            "voicelanguage", "Voice",
            RadioSettingValueList(VOICE_LIST,
                                  VOICE_LIST[_settings.voicelanguage]))
        basic.append(rs)

        rs = RadioSetting("scan", "Scan",
                          RadioSettingValueBoolean(_settings.scan))
        basic.append(rs)

        rs = RadioSetting("vox", "VOX",
                          RadioSettingValueBoolean(_settings.vox))
        basic.append(rs)

        rs = RadioSetting("voxlevel", "VOX level",
                          RadioSettingValueInteger(0, 4, _settings.voxlevel))
        basic.append(rs)

        rs = RadioSetting("voxinhibitonrx", "Inhibit VOX on receive",
                          RadioSettingValueBoolean(_settings.voxinhibitonrx))
        basic.append(rs)

        rs = RadioSetting("lowvolinhibittx", "Low volume inhibit transmit",
                          RadioSettingValueBoolean(_settings.lowvolinhibittx))
        basic.append(rs)

        rs = RadioSetting("highvolinhibittx", "High volume inhibit transmit",
                          RadioSettingValueBoolean(_settings.highvolinhibittx))
        basic.append(rs)

        rs = RadioSetting("alarm", "Alarm",
                          RadioSettingValueBoolean(_settings.alarm))
        basic.append(rs)

        # TODO: This should probably be called “FM Broadcast Band Radio”
        # or something. I'm not sure if the model actually has one though.
        rs = RadioSetting("fmradio", "FM Radio",
                          RadioSettingValueBoolean(_settings.fmradio))
        basic.append(rs)

        rs = RadioSetting("beep", "Beep",
                          RadioSettingValueBoolean(_settings2.beep))
        basic.append(rs)

        rs = RadioSetting("batterysaver", "Battery saver",
                          RadioSettingValueBoolean(_settings2.batterysaver))
        basic.append(rs)

        rs = RadioSetting("squelchlevel", "Squelch level",
                          RadioSettingValueInteger(0, 9,
                                                   _settings2.squelchlevel))
        basic.append(rs)

        rs = RadioSetting(
            "sidekeyfunction", "Sidekey function",
            RadioSettingValueList(SIDEKEYFUNCTION_LIST,
                                  SIDEKEYFUNCTION_LIST[
                                      _settings2.sidekeyfunction]))
        basic.append(rs)

        rs = RadioSetting(
            "timeouttimer", "Timeout timer",
            RadioSettingValueList(TIMEOUTTIMER_LIST,
                                  TIMEOUTTIMER_LIST[_settings2.timeouttimer]))
        basic.append(rs)

        return basic
