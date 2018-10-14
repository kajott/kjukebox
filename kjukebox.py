#!/usr/bin/env python2
"""
A simple audio and video 'jukebox' tool that can play files in a folder using
any installed common video player (MPV, VLC, MPC-HC, and Raspberry Pi's
OMXPlayer) with a web-based interface to set up the playlist.

When the playlist is empty, random tracks are played, with a selection biased
towards lesser-played tracks.

Position display inside tracks and seeking is currently not possible.
"""
__version__ = "1.0.6"
__author__ = "Martin Fiedler <keyj@emphy.de>"

import sys, os, re, argparse, random, collections, math
import time, threading, subprocess, socket
import BaseHTTPServer, SocketServer
import zlib
try:
    import _winreg
except ImportError:
    _winreg = None

DefaultPort = 8088
AcceptedExts = "mp4 m4v mov mkv webm mpg ts mts m2ts m2t ogv avi wmv asf".split() \
             + "mp3 ogg oga m4a mka wma wav aif aiff flac".split()
PollInterval = 0.2
MinAcceptedPlayTime = 3.0
MaxUnsuccessfulPlays = 5
MinPlayTime = 120
DefaultStateFile = ".kjukebox_state"
DefaultHistoryDepth = 250

################################################################################

def is_local_ip(ip):
    return ip.startswith("127.") or (ip == "::1")

def get_own_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.255.255.255', 1))
        ip = s.getsockname()[0]
    except EnvironmentError:
        ip = socket.gethostbyname(socket.gethostname())
    s.close()
    if is_local_ip(ip) and sys.platform.startswith('linux'):
        # if we're on a network without Internet connection, the above
        # methods will fail, and we're forced to do what we tried to
        # avoid until now: parse the output of "ip addr" ...
        try:
            iplist, dummy = subprocess.Popen(["ip", "addr", "show"], stdout=subprocess.PIPE).communicate()
        except EnvironmentError:
            iplist = ""
        iplist = [ip for ip in re.findall(r'^\s+inet\s+([0-9.]+)', iplist, flags=re.M) if not is_local_ip(ip)]
        if len(iplist) == 1:
            ip = iplist[0]
    return ip

g_nulldev = None
def nulldev():
    global g_nulldev
    if not g_nulldev:
        g_nulldev = open(("nul" if (sys.platform == "win32") else "/dev/null"), "wb")
    return g_nulldev

logfile = None
logmtx = threading.Lock()
def log(msg=None, to_stderr=False):
    if not msg:
        return
    if to_stderr:
        print >>sys.stderr, msg
    if not logfile:
        return
    with logmtx:
        logfile.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
        logfile.flush()

def find_binary(name):
    if os.access(name, os.X_OK):
        return
    if (sys.platform == "win32") and not(name.lower().endswith(".exe")):
        name += ".exe"
    for base in [".", os.path.dirname(sys.argv[0])] + filter(None, os.getenv("PATH").split(os.path.pathsep)):
        path = os.path.join(base, name)
        if os.access(path, os.X_OK):
            return os.path.normpath(os.path.abspath(path))
    if _winreg:
        try:
            key = _winreg.OpenKey(_winreg.HKEY_CLASSES_ROOT, "Applications\\" + name + "\\shell\\open\\command", 0, _winreg.KEY_READ)
            path = _winreg.QueryValueEx(key, None)[0]
            _winreg.CloseKey(key)
            if path.startswith('"'):
                return path.split('"', 2)[1]
            else:
                return path.split(' ', 1)[0]
        except EnvironmentError:
            pass

def get_console_size():
    import struct
    try:  # POSIX
        import fcntl, termios
        h, w = struct.unpack('HH4x', fcntl.ioctl(sys.stdout.fileno(), termios.TIOCGWINSZ, '\0' * 8))
        return (w, h)
    except ImportError:
        pass
    try:  # Win32
        import ctypes
        buf = ctypes.create_string_buffer(22)
        if ctypes.windll.kernel32.GetConsoleScreenBufferInfo(ctypes.windll.kernel32.GetStdHandle(-12), buf):
            l, t, r, b = struct.unpack("10x4h4x", buf.raw)
            return (r - l + 1, b - t + 1)
    except (ImportError, AttributeError):
        pass
    return (80, 24)  # default fallback

################################################################################

Players = map(str.strip, """
    omxplayer.bin -b $
    omxplayer -b $
    mpc-hc64 $ /play /fullscreen? /close
    mpc-hc $ /play /fullscreen? /close
    mpv --really-quiet --fs? $
    vlc --quiet --fullscreen? --no-random --no-repeat --no-video-title-show $ vlc://quit
    mplayer -fs? $
""".strip().split('\n'))

def setup_player(name=None, fullscreen=True):
    if name:
        full = find_binary(name)
        if full:
            name = full
            args = []
        else:
            args = filter(None, name.split())
            name = find_binary(args.pop(0))
    else:
        args = []
        for p in Players:
            name = find_binary(p.strip().split()[0])
            if name: break
    if not name:
        return
    base = os.path.splitext(os.path.basename(name))[0].lower()
    for p in Players:
        p = filter(None, p.split())
        if base == p[0]:
            cmdline = [name] + [x.rstrip('?') for x in p[1:] if (fullscreen or not(x.endswith('?')))]
            i = cmdline.index('$')
            cmdline[i:i] = args
            return cmdline

################################################################################

StatusFont = dict(zip((
'0'    ,'1'  ,'2'    ,'3'    ,'4'    ,'5'    ,'6'    ,'7'    ,'8'    ,'9'    ,'.' ,':' ,'http://'
,'\0'), zip(*(line.split('j') for line in unicode(r'''
  ###  j  #  j  ###  j ##### j   #   j ##### j  ###  j ##### j  ###  j  ###  j   j   j #      #   #           #  # j
 #   # j ##  j #   # j    #  j  #    j #     j #     j #   # j #   # j #   # j   j   j #      #   #           #  # j
 #   # j  #  j    #  j  ###  j #  #  j ####  j ####  j    #  j  ###  j #   # j   j # j # ##  ### ### ###  #  #  #  j
 #   # j  #  j   #   j     # j ##### j     # j #   # j   #   j #   # j  #### j   j   j ##  #  #   #  #  #    #  #  j
 #   # j  #  j  #    j #   # j    #  j #   # j #   # j   #   j #   # j     # j   j   j #   #  #   #  ###    #  #   j
  ###  j ### j ##### j  ###  j    #  j  ###  j  ###  j   #   j  ###  j  ###  j # j # j #   #   #   # #    # #  #   j
       j     j       j       j       j       j       j       j       j       j   j   j               #             j
'''.replace('\r', '').strip('\n')).replace('#', unichr(0x2592)).split('\n')))))
StatusFontHeight = len(StatusFont.values()[0])

class Distributor(object):
    def __init__(self, n):
        self.n = n
    def get(self, part=1):
        p = int(float(self.n) / part + 0.5)
        self.n -= p
        return p

class StatusScreen(object):
    @classmethod
    def init(self, text=None, logofile=None):
        w, h = get_console_size()
        self.width = w - 1
        self.height = h - 1

        if logofile:
            self.inter_lines = self._load_file(logofile)
        elif text:
            self.inter_lines = self._render_text(text)
        else:
            self.inter_lines = ""

        # distribute extra lines
        d = Distributor(max(self.height - 6 - self.inter_lines.count('\n'), 0))
        self.inter_lines = (d.get(4) * "\n") + self.inter_lines + (d.get(3) * "\n")
        self.pre_gap = d.get(2) * "\n"
        self.post_gap = d.get() * "\n"

    @classmethod
    def _load_file(self, filename):
        try:
            with open(filename, "rb") as f:
                data = f.read()
        except EnvironmentError:
            return ""
        try:
            data = unicode(data, 'utf-8')
        except UnicodeDecodeError:
            data = unicode(data, 'windows-1252')
        lines = data.split('\n')
        if data.endswith('\n'):
            del lines[-1]
        if not lines:
            return ""
        lines = map(unicode.rstrip, lines)
        extra = max((self.width - max(map(len, lines))) / 2, 0) * u' '
        return u'\n'.join(extra + l[:self.width] for l in lines) + u'\n'

    @classmethod
    def _render_text(self, text):
        # "render" all parts of the big text into strings
        parts = []
        for part in text.split('\0'):
            rows = [""] * StatusFontHeight
            while part:
                try:
                    l, g = max((len(g), g) for g in StatusFont if part.startswith(g))
                    part = part[l:]
                    for i, x in enumerate(StatusFont[g]):
                        rows[i] += x
                except ValueError:
                    part = part[1:]
            parts.append(rows)

        # join parts if they fit into a line
        i = 1
        while i < len(parts):
            if (len(parts[i-1][0]) + len(parts[i][0])) <= self.width:
                parts[i-1] = [a+b for a,b in zip(parts[i-1], parts[i])]
                del parts[i]
            else:
                i += 1
        extra_line = self.height >= (len(parts) * (StatusFontHeight + 1) + 12)

        # glue parts together into a string
        lines = []
        for p in parts:
            extra = max((self.width - len(p[0])) / 2, 0) * u' '
            lines.extend((extra + l[-self.width:].rstrip()) for l in p)
            if extra_line:
                lines.append("")
        return u'\n'.join(lines).rstrip() + u'\n'

    @classmethod
    def substatus(self, caption, fill, f):
        if not f:
            return sys.stdout.write("\n\n\n")
        name = f.label.replace(u'\xa0', ' ').replace(u'\u25ba', '>').replace(u'\u2014', '--').encode(sys.stdout.encoding, 'replace')
        if len(name) > self.width:
            name = "..." + name[3-self.width:]
        else:
            name = (((self.width - len(name)) / 2) * " ") + name
        sys.stdout.write("%s\n%s\n%s\n" % (caption.center(self.width, fill), name, self.width * fill))

    @classmethod
    def update(self, prev=None, next=None):
        if sys.platform == "win32":
            sys.stdout.write((self.height + 1) * '\n')
        else:
            sys.stdout.write("\x1b[2J\x1b[H")
        sys.stdout.write(self.pre_gap)
        self.substatus(" PREVIOUSLY ", '-', prev)
        sys.stdout.write(self.inter_lines)
        self.substatus(" UP NOW ", '=', next)
        sys.stdout.write(self.post_gap)
        sys.stdout.flush()

################################################################################

class MediaFile(object):
    def __init__(self, path, key=None):
        self.path = path
        self.key = key or self.make_key(path)
        self.label = unicode(os.path.splitext(path)[0].replace('\\', '/'), sys.getfilesystemencoding(), 'replace') \
                     .replace('/', u'\xa0\u25ba ').replace('--', u'\u2014')
        self.present = True

    def __repr__(self):
        return "MediaFile(%r)" % self.path

    def fmt(self, prefix=""):
        return "%s%d\t%s" % (prefix, id(self), self.label.encode('utf-8'))

    def _index_entry(self):
        self.present = False
        return (self.key, self)
    def _mark_present(self, new_path=None):
        if new_path:
            self.path = new_path
        self.present = True

    @staticmethod
    def make_key(name):
        return name.replace('\\', '/').lower()
    @staticmethod
    def make_path_key(path):
        return os.path.splitext(path)[0].replace('\\', '/').lower()

class ListManager(object):
    root = '.'
    mutex = threading.Lock()
    files = []
    current = None
    playlist = []
    history = []
    playcounts = collections.defaultdict(int)
    statefile = DefaultStateFile
    is_auto_playlist = False
    running = False
    player = None
    cmdline = []
    fail_count = 0
    started_at = None
    autoscan = False
    autosave = (sys.platform == "win32")
    scan_tag = None
    maxhist = DefaultHistoryDepth
    retcode = None
    first_in_session = True
    u_tracklist = None
    z_tracklist = None

    @classmethod
    def load_state(self, filename=None):
        with self.mutex:
            if filename:
                self.statefile = filename
            self.history = []
            self.playlist = []
            self.is_auto_playlist = False
            try:
                with open(self.statefile) as state:
                    lineno = 0
                    for line in state:
                        lineno += 1
                        line = line.strip()
                        if line.startswith('-'):
                            self._locked_search(line[1:], self.history)
                        elif line.startswith('+'):
                            self._locked_search(line[1:], self.playlist)
                        elif line.startswith('=') and ('*' in line):
                            c, n = map(str.strip, line[1:].split('*', 1))
                            try:
                                self.playcounts[MediaFile.make_key(n)] = int(c)
                            except ValueError:
                                pass
                        elif line and not(line.startswith(('#', ';'))):
                            print >>sys.stderr, "syntax error in %s:%d: '%s'" % (self.statefile, lineno, line)
            except EnvironmentError:
                pass
            self._locked_refill()

    @classmethod
    def save_state(self, filename=None, sort=True):
        with self.mutex:
            if filename:
                self.statefile = filename
            self._locked_save(sort=sort)
    @classmethod
    def _locked_save(self, sort=True):
        try:
            with open(self.statefile, "w") as state:
                state.write("# kjukebox %s state [%s]\n\n" % (__version__, time.strftime("%Y-%m-%d %H:%M:%S")))
                if self.history or (self.playlist and not(self.is_auto_playlist)):
                    state.write("# history and playlist\n")
                for f in self.history[-self.maxhist:]:
                    state.write("-%s\n" % f.key)
                if not self.is_auto_playlist:
                    for f in self.playlist:
                        state.write("+%s\n" % f.key)
                if self.playcounts:
                    state.write("\n# play count information\n")
                if sort:
                    for n, c in sorted(self.playcounts.iteritems()):
                        if c:
                            state.write("=%d*%s\n" % (c, n))
                else:
                    for n, c in self.playcounts.iteritems():
                        if c:
                            state.write("=%d*%s\n" % (c, n))
        except EnvironmentError, e:
            log("WARNING: failed to save play counts - %s" % e, True)

    @classmethod
    def set_root(self, path):
        self.root = os.path.normpath(os.path.abspath(path))

    @classmethod
    def rescan(self):
        with self.mutex:
            self._locked_rescan()
    @classmethod
    def _locked_rescan(self):
        index = dict(f._index_entry() for f in self.files)
        n_new = 0
        for base, dirs, files in os.walk(self.root):
            assert base.startswith(self.root)
            base = base[len(self.root):].lstrip('\\/')
            for f in files:
                if not(f.startswith('.')) and (os.path.splitext(f)[-1].strip('.').lower() in AcceptedExts):
                    f = os.path.join(base, f)
                    key = MediaFile.make_path_key(f)
                    try:
                        index[key]._mark_present(f)
                    except KeyError:
                        f = MediaFile(f, key)
                        self.files.append(f)
                        index[key] = f
                        n_new += 1
        n_del = len(self.files)
        self.files = [f for f in self.files if f.present]
        n_del -= len(self.files)
        self.files.sort(key=lambda f: f.key)
        self.playlist = [f for f in self.playlist if f.present]
        if n_new or n_del:
            log("rescan finished: %d new track(s), %d track(s) deleted" % (n_new, n_del))
            self.scan_tag = str(int(time.time()))
            self.u_tracklist = '\n'.join(f.fmt() for f in self.files)
            self.z_tracklist = zlib.compress(self.u_tracklist, 9)
        self._locked_refill()

    @classmethod
    def get_tracklist(self):
        with self.mutex:
            for f in self.files:
                yield f.fmt()

    @classmethod
    def get_tracklist_str(self, deflate=False):
        with self.mutex:
            return self.z_tracklist if deflate else self.u_tracklist

    @classmethod
    def get_playlist(self):
        with self.mutex:
            if self.current:
                yield self.current.fmt('+')
            prefix = '-' if self.is_auto_playlist else ''
            for f in self.playlist:
                yield f.fmt(prefix)

    @classmethod
    def get_history(self):
        with self.mutex:
            for f in self.history:
                yield f.fmt()
            if self.current:
                yield self.current.fmt('+')

    @classmethod
    def _locked_lookup(self, iid):
        if isinstance(iid, MediaFile):
            return iid
        try:
            iid = int(iid)
        except ValueError:
            return
        for f in self.files:
            if id(f) == iid:
                return f

    @classmethod
    def _locked_search(self, name, append_to=None):
        key = MediaFile.make_key(name)
        for f in self.files:
            if f.key == key:
                if not(append_to is None):
                    append_to.append(f)
                return f

    @classmethod
    def _locked_checkpoint(self):
        if self.autosave:
            self._locked_save()
        if self.autoscan:
            self._locked_rescan()

    @classmethod
    def add_to_front(self, iid):
        with self.mutex:
            f = self._locked_lookup(iid)
            if not f: return
            self._locked_add_to_front(f)
    @classmethod
    def _locked_add_to_front(self, f):
        try:
            self.playlist.remove(f)
        except ValueError:
            pass
        if self.is_auto_playlist:
            self.playlist = [f]
            self.is_auto_playlist = False
        else:
            self.playlist.insert(0, f)

    @classmethod
    def add_to_back(self, iid):
        with self.mutex:
            f = self._locked_lookup(iid)
            if not f: return
            if self.is_auto_playlist or not(self.playlist):
                self.playlist = [f]
                self.is_auto_playlist = False
            else:
                self.playlist.append(f)

    @classmethod
    def _locked_refill(self):
        if self.playlist:
            return  # playlist still populated
        # generate list of all files (except current and those in history)
        filelist = [f for f in self.files if (f != self.current) and not(f in self.history)]
        # if empty, try again with, now including history
        if not filelist:
            filelist = [f for f in self.files if (f != self.current)]
        if not filelist:
            return  # still empty -> there's no file to select at all
        # draw some files from the pool
        filelist = random.sample(filelist, max(1, int(math.sqrt(len(filelist)) + 0.9)))
        # decorate list with play count and random number, select minimum
        self.playlist = [min((self.playcounts[f.key], random.random(), f) for f in filelist)[-1]]
        self.is_auto_playlist = True

    @classmethod
    def remove_file(self, iid):
        with self.mutex:
            f = self._locked_lookup(iid)
            if not f: return
            if f == self.current:
                return self._locked_next()
            try:
                self.playlist.remove(f)
            except ValueError:
                return  # item not found
            self._locked_refill()

    @classmethod
    def _locked_stop(self, return_to_playlist=False, always_add_to_playcounts=False):
        if self.current:
            log("stopping '%s'" % self.current.path)
            if not return_to_playlist:
                self.history.append(self.current)
            elif self.is_auto_playlist:
                self.playlist = [self.current]
            else:
                self.playlist.insert(0, self.current)
            if not self.running:
                StatusScreen.update(prev=self.current)
            if always_add_to_playcounts or not(self.started_at) or ((time.time() - self.started_at) >= MinPlayTime):
                self.playcounts[self.current.key] += 1
            else:
                log("not adding to playcounts (only played for %.1f seconds)" % (time.time() - self.started_at))
            if not self.running:
                self._locked_checkpoint()
            self.current = None
        if self.player:
            log("killing player executable")
            timeout = time.time() + 2.0
            kill = True
            if ("omxplayer" in self.cmdline[0]) and not("omxplayer.bin" in self.cmdline[0]):
                kill = bool(subprocess.call(["killall", "-2", "omxplayer.bin"]))
            if kill:
                self.player.send_signal(15 if (sys.platform == "win32") else 2)
            while self.player.poll() is None:
                if time.time() > timeout:
                    log("ERROR: failed to kill player", True)
                    break
                time.sleep(0.01)
            self.player = None
        self.started_at = None

    @classmethod
    def _locked_play(self, set_running=False):
        if self.current or self.player:
            log("INTERNAL ERROR: attempt to play track while another is still playing", True)
            return
        self._locked_refill()
        if not self.playlist:
            self.running = False
            return
        self.current = self.playlist[0]
        StatusScreen.update(prev=(self.history[-1] if (self.history and not(self.first_in_session)) else None),
                            next=self.current)
        self.first_in_session = False
        log("playing '%s'" % self.current.path)
        self._locked_checkpoint()
        del self.playlist[0]
        self._locked_refill()
        if set_running:
            self.running = True
        path = os.path.join(self.root, self.current.path)
        cmdline = [(path if (x == '$') else x) for x in self.cmdline]
        pretty_cmdline = ' '.join((('"%s"' % x) if (' ' in x) else x) for x in cmdline)
        log("+ " + pretty_cmdline)
        try:
            self.player = subprocess.Popen(cmdline, stdin=nulldev(), stdout=(logfile or nulldev()), stderr=subprocess.STDOUT)
            self.started_at = time.time()
        except EnvironmentError, e:
            log("ERROR: failed to start video player - %s" % e, True)
            print >>sys.stderr, "Failed command line was:"
            print >>sys.stderr, "  " + pretty_cmdline
            self.running = False
            self.player = None
            self._locked_stop(True)

    @classmethod
    def next(self):
        with self.mutex:
            self._locked_next(True)
    @classmethod
    def _locked_next(self, force_play=False):
        self._locked_stop()
        if force_play or self.running:
            self._locked_play(True)

    @classmethod
    def prev(self):
        with self.mutex:
            if not self.history:
                return
            self._locked_stop(True)
            self.playlist.insert(0, self.history.pop())
            self.is_auto_playlist = False
            self._locked_play(True)

    @classmethod
    def play(self):
        with self.mutex:
            self._locked_stop(True)
            self._locked_play(True)

    @classmethod
    def stop(self):
        with self.mutex:
            self.running = False
            self._locked_stop()

    @classmethod
    def play_specific(self, iid):
        with self.mutex:
            f = self._locked_lookup(iid)
            if not f: return
            self._locked_add_to_front(f)
            self.running = True
            self._locked_stop()
            self._locked_play()

    @classmethod
    def rewind_to(self, iid):
        with self.mutex:
            f = self._locked_lookup(iid)
            if not(f) or not(f in self.history): return
            self._locked_stop(True)
            idx = max(i for i, xf in enumerate(self.history) if f == xf)
            self.playlist[:0] = self.history[idx:]
            del self.history[idx:]
            self.is_auto_playlist = False
            if self.running:
                self._locked_play()

    @classmethod
    def tick(self):
        with self.mutex:
            if self.player:
                ret = self.player.poll()
                if not(ret is None):
                    log("player executable stopped")
                    ok = not(self.started_at) or ((time.time() - self.started_at) > MinAcceptedPlayTime)
                    if ok:
                        self.fail_count = 0
                    else:
                        self.fail_count += 1
                        log("WARNING: player exited suspiciously quickly (%d in a row)" % self.fail_count)
                        if self.fail_count >= MaxUnsuccessfulPlays:
                            log("WARNING: playback failed suspiciously often, stopping", True)
                            self.running = False
                    self.player = None
                    self._locked_stop(always_add_to_playcounts=ok)
                    self._locked_next()

    @classmethod
    def quit(self, code=0):
        with self.mutex:
            log("exit with return code %d requested" % code)
            if (self.retcode is None) or (code > self.retcode):
                self.retcode = code

################################################################################

StaticHTMLContent = {

"": ("text/html; charset=utf-8", r'''<!DOCTYPE html>
<html><head>
<meta name="viewport" content="width=device-width,initial-scale=1,user-scalable=no" />
<title>Jukebox Web Interface</title>
<script type="text/javascript" src="script.js"></script>
<link rel="stylesheet" type="text/css" href="style.css" />
</head><body onload="init()">
<div id="buttons"><div
onClick="setMode('browse')" title="add tracks to playlist"></div><div
style="background-position-x:-70px;" onClick="setMode('playlist')" title="show playlist"></div><div
style="background-position-x:-140px;" onClick="setMode('history')" title="show history"></div><div
style="background-position-x:-210px;" onClick="sendCmd('/prev')" title="go to previous track"></div><div
style="background-position-x:-280px;" onClick="sendCmd('/play')" title="start playback or replay current track"></div><div
style="background-position-x:-350px;" onClick="sendCmd('/stop')" title="stop playback"></div><div
style="background-position-x:-420px;" onClick="sendCmd('/next')" title="go to next track"></div></div><div
id="main"><input id="search" type="text" onchange="updateSearch()" oninput="updateSearch()"></input><ul
id="list"></ul></div></body></html>
'''),

"script.js": ("text/javascript", r'''
var g_currentMode;
var menuItems = {
    "browse": [
        { cmd:"/playnow?",  icon:"play",  text:"play now" },
        { cmd:"/insert?",   icon:"front", text:"play next" },
        { cmd:"/add?",      icon:"add",   text:"append to playlist" }
    ],
    "playlist": [
        { cmd:"/playnow?",  icon:"play",  text:"play now" },
        { cmd:"/insert?",   icon:"front", text:"play next" },
        { cmd:"/remove?",   icon:"del",   text:"remove from playlist" }
    ],
    "history": [
        { cmd:"/rollback?", icon:"front", text:"rewind to here" },
    ]
};

function setVisible(node, visible) {
    node.style.display = visible ? "" : "none";
}

function updateSearch() {
    var terms = document.getElementById("search").value.toLowerCase().split(" ").filter(word => word);
    var items = document.getElementById("list").children;
    for (var i = 0;  i < items.length;  ++i) {
        var node = items[i];
        var text = node.textContent.toLowerCase();
        var ok = true;
        for (var j = 0;  j < terms.length;  ++j) {
            if (text.indexOf(terms[j]) < 0) {
                ok = false;
                break;
            }
        }
        setVisible(node, ok);
    }
}

function makeNode(text, action=null, classes=null) {
    var node = document.createElement("li");
    if (classes) { node.className = classes; }
    if (action) { node.addEventListener('click', action); }
    node.appendChild(document.createTextNode(text));
    return node;
}

function sendCmd(url) {
    var req = new XMLHttpRequest();
    req.open("GET", url, false);
    req.send();
    if (g_currentMode != "browse") {
        setMode(g_currentMode);
    }
}

function hideMenu() {
    var parent = document.getElementById("list");
    var next = parent.firstChild;
    var currentMenu = null;
    while (next) {
        var curr = next;
        next = curr.nextSibling;
        if (curr.classList.contains("menu")) {
            parent.removeChild(curr);
        } else if (curr.classList.contains("selected")) {
            currentMenu = curr;
            curr.classList.remove("selected");
        }
    }
    return currentMenu;
}

function onMenuItemClick(ev) {
    var node = ev.target;
    sendCmd(node.getAttribute('data-cmd'));
    if (g_currentMode == "browse") {
        hideMenu();
    }
}

function onListItemClick(ev) {
    var node = ev.target;
    if (hideMenu() == node) {
        // when clicking on an already open menu again, hide it
        return;
    }
    if (node.classList.contains("playing") || node.classList.contains("autoplay")) {
        // don't show menus on special items
        return;
    }
    var parent = node.parentNode;
    var next = node.nextSibling;
    var iid = node.getAttribute('data-id');
    node.classList.add("selected");
    var menu = menuItems[g_currentMode];
    for (var i = 0;  i < menu.length;  i++) {
        var item = menu[i];
        node = makeNode(item.text, onMenuItemClick, "menu i" + item.icon);
        node.setAttribute('data-cmd', item.cmd + iid);
        parent.insertBefore(node, next);
    }
    var rect = node.getBoundingClientRect();
    if (rect.bottom > (window.innerHeight || document.documentElement.clientHeight)) {
        node.scrollIntoView(false);
    }
}

function populateList(data) {
    var list = document.getElementById("list");
    var node = null;
    data.split('\n').forEach(function(rawitem) {
        if (!rawitem) { return; }
        var item = rawitem.split('\t');
        if ((item.length < 2) || !item[0] || !item[1]) { return; }
        var iid = item[0];
        var cls = null;
        if (iid.substr(0, 1) == "+") { cls = "playing";  iid = iid.substr(1); }
        if (iid.substr(0, 1) == "-") { cls = "autoplay"; iid = iid.substr(1); }
        node = makeNode(item[1], onListItemClick, cls);
        node.setAttribute('data-id', iid);
        list.appendChild(node);
    })
    if (g_currentMode == "browse") {
        updateSearch();
    }
    if (node && (g_currentMode == "history")) {
        node.scrollIntoView(false);
    }
}

function setMode(mode) {
    var listURL = "/" + mode;
    var listEvent = null;
    var searchBox = document.getElementById("search");
    var searchVisible = false;
    if ((mode != "history") && (mode != "playlist")) {
        mode = "browse";
        listURL = "/tracklist";
        searchVisible = true;
        searchBox.value = "";
    }
    g_currentMode = mode;
    window.location.hash = mode;
    setVisible(searchBox, searchVisible);
    
    // clear and reload list
    var list = document.getElementById("list");
    while (list.hasChildNodes()) {
        list.removeChild(list.firstChild);
    }
    var req = new XMLHttpRequest();
    req.onreadystatechange = function() {
        if ((this.readyState == 4) && (this.status == 200)) {
            populateList(this.responseText);
        }
    }
    req.open("GET", listURL);
    req.send();
}

function init() {
    var mode = window.location.hash.toLowerCase();
    if (mode.substr(0, 1) == '#') { mode = mode.substr(1); }
    setMode(mode);
}
'''),

"style.css": ("text/css", r'''
* {
    font-family: "Fira Sans","Noto Sans","Source Sans Pro","Myriad Pro","Segoe UI","Droid Sans","DejaVu Sans",Verdana,"Helvetica Neue",sans-serif;
    font-size: 16px;
}
body {
    margin: 0;
    padding: 0;
    overflow-x: hidden;
    overflow-y: scroll;
    background-color: #eee;
}
#buttons {
    position: fixed;
    top: 0;
    width: 100%;
    text-align: center;
    padding: 8px 0 8px 0;
    margin: 0;
    background-color: #ccc;
    border-bottom: solid 1px #888;
}
#buttons > div {
    display: inline-block;
    width: 64px;
    height: 64px;
    background: url(icons.svg) no-repeat;
    margin: 0 4px 0 4px;
    cursor: pointer;
}
input {
    display: block;
    width: 100%;
    border: none;
    border-bottom: solid 1px #ccc;
    padding: 4px 4px 4px 22px;
    background: url(icons.svg) no-repeat 0px -280px;
    background-color: #ffe;
}
#main {
    margin: 85px 0 0 0;
    padding: 0;
}
#list {
    list-style: none;
    margin: 0;
    padding: 0;
}
li {
    background-color: white;
    margin: 0;
    padding: 4px;
    border-bottom: solid 1px #ccc;
    cursor: pointer;
}
li.selected {
    background-color: #def;
}
li.menu {
    padding-left: 32px;
    background: url(icons.svg) no-repeat -9999px -9999px;
    background-color: #def;
}
li.iplay  { background-position: -400px -160px; }
li.iadd   { background-position: -300px -190px; }
li.ifront { background-position: -200px -220px; }
li.idel   { background-position: -100px -250px; }
li:not(.selected):hover {
    background-color: #f0f8ff;
}
li.menu:hover {
    background-color: #cdf;
}
li.playing {
    font-weight: bold;
}
li.autoplay {
    color: #888;
}
@media screen and (max-width: 520px) {
    #buttons {
        height: 48px;
    }
    #buttons > div {
        width: 48px;
        height: 48px;
        background-position-y:-70px;
    }
    #main {
        margin-top: 65px;
    }
}
@media screen and (max-width: 408px) {
    #buttons {
        height: 32px;
    }
    #buttons > div {
        width: 32px;
        height: 32px;
        background-position-y:-120px;
    }
    #main {
        margin-top: 49px;
    }
}
'''),

"icons.svg": ("image/svg+xml", r'''<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<svg xmlns="http://www.w3.org/2000/svg" width="490" height="310">
<defs>
<style type="text/css">
* { stroke:none; fill-opacity:0.75; fill:white; }
#menu path { fill:black; }
</style>
<circle id="c" cx="32" cy="32" r="31" style="fill:black" />
<g id="b0"><use href="#c" /><path d="m 19.5,16 c -0.831,0 -1.5,0.669 -1.5,1.5 0,0.831 0.669,1.5 1.5,1.5 h 25 c 0.831,0 1.5,-0.669 1.5,-1.5 0,-0.831 -0.669,-1.5 -1.5,-1.5 z m 0,6 c -0.831,0 -1.5,0.669 -1.5,1.5 0,0.831 0.669,1.5 1.5,1.5 h 25 c 0.831,0 1.5,-0.669 1.5,-1.5 0,-0.831 -0.669,-1.5 -1.5,-1.5 z m 0,6 c -0.831,0 -1.5,0.669 -1.5,1.5 0,0.831 0.669,1.5 1.5,1.5 h 25 c 0.831,0 1.5,-0.669 1.5,-1.5 0,-0.831 -0.669,-1.5 -1.5,-1.5 z m 28.5,2 v 6 h -6 v 4 h 6 v 6 h 4 v -6 h 6 v -4 h -6 v -6 z m -28.5,4 c -0.831,0 -1.5,0.669 -1.5,1.5 0,0.831 0.669,1.5 1.5,1.5 h 17 c 0.831,0 1.5,-0.669 1.5,-1.5 0,-0.831 -0.669,-1.5 -1.5,-1.5 z m 0,6 c -0.831,0 -1.5,0.669 -1.5,1.5 0,0.831 0.669,1.5 1.5,1.5 h 17 c 0.831,0 1.5,-0.669 1.5,-1.5 0,-0.831 -0.669,-1.5 -1.5,-1.5 z m 0,6 c -0.831,0 -1.5,0.669 -1.5,1.5 0,0.831 0.669,1.5 1.5,1.5 h 25 c 0.831,0 1.5,-0.669 1.5,-1.5 0,-0.831 -0.669,-1.5 -1.5,-1.5 z" /></g>
<g id="b1"><use href="#c" /><path d="m 17,17.5 -6,5 v -10 z M 19.5,16 c -0.831,0 -1.5,0.669 -1.5,1.5 0,0.831 0.669,1.5 1.5,1.5 h 25 c 0.831,0 1.5,-0.669 1.5,-1.5 0,-0.831 -0.669,-1.5 -1.5,-1.5 z m 0,6 c -0.831,0 -1.5,0.669 -1.5,1.5 0,0.831 0.669,1.5 1.5,1.5 h 25 c 0.831,0 1.5,-0.669 1.5,-1.5 0,-0.831 -0.669,-1.5 -1.5,-1.5 z m 0,6 c -0.831,0 -1.5,0.669 -1.5,1.5 0,0.831 0.669,1.5 1.5,1.5 h 25 c 0.831,0 1.5,-0.669 1.5,-1.5 0,-0.831 -0.669,-1.5 -1.5,-1.5 z m 0,6 c -0.831,0 -1.5,0.669 -1.5,1.5 0,0.831 0.669,1.5 1.5,1.5 h 25 c 0.831,0 1.5,-0.669 1.5,-1.5 0,-0.831 -0.669,-1.5 -1.5,-1.5 z m 0,6 c -0.831,0 -1.5,0.669 -1.5,1.5 0,0.831 0.669,1.5 1.5,1.5 h 25 c 0.831,0 1.5,-0.669 1.5,-1.5 0,-0.831 -0.669,-1.5 -1.5,-1.5 z m 0,6 c -0.831,0 -1.5,0.669 -1.5,1.5 0,0.831 0.669,1.5 1.5,1.5 h 25 c 0.831,0 1.5,-0.669 1.5,-1.5 0,-0.831 -0.669,-1.5 -1.5,-1.5 z" /></g>
<g id="b2"><use href="#c" /><path d="m 19.5,16 c -0.831,0 -1.5,0.669 -1.5,1.5 0,0.831 0.669,1.5 1.5,1.5 h 25 c 0.831,0 1.5,-0.669 1.5,-1.5 0,-0.831 -0.669,-1.5 -1.5,-1.5 z m 0,6 c -0.831,0 -1.5,0.669 -1.5,1.5 0,0.831 0.669,1.5 1.5,1.5 h 25 c 0.831,0 1.5,-0.669 1.5,-1.5 0,-0.831 -0.669,-1.5 -1.5,-1.5 z m 0,6 c -0.831,0 -1.5,0.669 -1.5,1.5 0,0.831 0.669,1.5 1.5,1.5 h 25 c 0.831,0 1.5,-0.669 1.5,-1.5 0,-0.831 -0.669,-1.5 -1.5,-1.5 z m 0,6 c -0.831,0 -1.5,0.669 -1.5,1.5 0,0.831 0.669,1.5 1.5,1.5 h 13 c 0.831,0 1.5,-0.669 1.5,-1.5 0,-0.831 -0.669,-1.5 -1.5,-1.5 z m 23.51562,0.014 a 9,9 0 0 0 -9,9 9,9 0 0 0 9,9 9,9 0 0 0 9,-9 9,9 0 0 0 -9,-9 z m 0,2 a 7,7 0 0 1 7,7 7,7 0 0 1 -7,7 7,7 0 0 1 -7,-7 7,7 0 0 1 7,-7 z m -0.0156,0.986 a 1,1 0 0 0 -0.98438,1.0136 v 3.1309 l -1.44531,-0.9629 a 1,1 0 1 0 -1.10937,1.6641 l 3,2 a 1,1 0 0 0 1.55468,-0.8321 v -5 A 1.0001,1.0001 0 0 0 43,37 Z m -23.5,3 c -0.831,0 -1.5,0.669 -1.5,1.5 0,0.831 0.669,1.5 1.5,1.5 h 11 c 0.831,0 1.5,-0.669 1.5,-1.5 0,-0.831 -0.669,-1.5 -1.5,-1.5 z m 0,6 c -0.831,0 -1.5,0.669 -1.5,1.5 0,0.831 0.669,1.5 1.5,1.5 h 11 c 0.831,0 1.5,-0.669 1.5,-1.5 0,-0.831 -0.669,-1.5 -1.5,-1.5 z" /></g>
<g id="b3"><use href="#c" /><path d="m 11,20 v 24 h 5 v -12 -12 z m 5,12 18,12 v -12 -12 z m 18,0 18,12 v -24 z" /></g>
<g id="b4"><use href="#c" /><path d="m 46,32 -24,-16 v 32 l 24,-16 z" /></g>
<g id="b5"><use href="#c" /><path d="m 20,20 v 24 h 24 v -24 z" /></g>
<g id="b6"><use href="#c" /><path d="m 12,20 v 24 l 18,-12 z m 18,12 v 12 l 18,-12 -18,-12 z m 18,0 v 12 h 5 v -24 h -5 z" /></g>
</defs>
<use href="#b0" transform="translate(  0 0)" />
<use href="#b0" transform="translate(  0 70) scale(0.75)" />
<use href="#b0" transform="translate(  0 120) scale(0.5)" />
<use href="#b1" transform="translate( 70 0)" />
<use href="#b1" transform="translate( 70 70) scale(0.75)" />
<use href="#b1" transform="translate( 70 120) scale(0.5)" />
<use href="#b2" transform="translate(140 0)" />
<use href="#b2" transform="translate(140 70) scale(0.75)" />
<use href="#b2" transform="translate(140 120) scale(0.5)" />
<use href="#b3" transform="translate(210 0)" />
<use href="#b3" transform="translate(210 70) scale(0.75)" />
<use href="#b3" transform="translate(210 120) scale(0.5)" />
<use href="#b4" transform="translate(280 0)" />
<use href="#b4" transform="translate(280 70) scale(0.75)" />
<use href="#b4" transform="translate(280 120) scale(0.5)" />
<use href="#b5" transform="translate(350 0)" />
<use href="#b5" transform="translate(350 70) scale(0.75)" />
<use href="#b5" transform="translate(350 120) scale(0.5)" />
<use href="#b6" transform="translate(420 0)" />
<use href="#b6" transform="translate(420 70) scale(0.75)" />
<use href="#b6" transform="translate(420 120) scale(0.5)" />
<g id="menu" transform="translate(12 6)">
<path transform="translate(400 160)" d="M 4,16 V 0 l 10,8 z" />
<path transform="translate(300 190)" d="M 6,1 V 6 H 1 v 4 h 5 v 5 h 4 v -5 h 5 V 6 H 10 V 1 Z" />
<path transform="translate(200 220)" d="M 3 0 C 1.9 0 1 0.9 1 2 C 1 3.1 1.9 4 3 4 L 8 4 L 13 4 C 14.1 4 15 3.1 15 2 C 15 0.9 14.1 0 13 0 L 3 0 z M 8 4 L 4 8 L 7 8 L 7 16 L 9 16 L 9 8 L 12 8 L 8 4 z" />
<path transform="translate(100 250)" d="M 1 6 L 1 10 L 15 10 L 15 6 L 1 6 z" />
</g>
<path transform="translate(4 287)" style="fill:black;fill-opacity:0.5;" d="M5,0C2.25,0 0,2.25 0,5 0,7.75 2.25,10 5,10 5.8643919,10 6.6637544,9.7652525 7.375,9.375L12,14 14,12 9.375,7.375C9.7652525,6.6637544 10,5.8643919 10,5 10,2.25 7.75,0 5,0 z M 5,2.5C6.375,2.5 7.5,3.625 7.5,5 7.5,6.375 6.375,7.5 5,7.5 3.625,7.5 2.5,6.375 2.5,5 2.5,3.625 3.625,2.5 5,2.5Z" />
</svg>'''),
}

DeflatedStaticHTMLContent = {}
def mod_gzip():
    for key, data in StaticHTMLContent.iteritems():
        DeflatedStaticHTMLContent[key] = zlib.compress(data[1])

################################################################################

class WebServer(SocketServer.ThreadingMixIn, BaseHTTPServer.HTTPServer):
    pass

def _get_etag():
    try:
        return str(int(os.path.getmtime(sys.argv[0])))
    except EnvironmentError:
        return str(int(time.time()))

class WebRequestHandler(BaseHTTPServer.BaseHTTPRequestHandler):
    etag = _get_etag()
    quitcmds = {}
    server_version = "kjukebox/" + __version__

    def do_GET(self):
        self._response_sent = False
        try:
            path, params = self.path.split('?', 1)
        except ValueError:
            path, params = self.path, None
        path = path.strip('/').lower()

        if path in StaticHTMLContent:
            if self.headers.get("If-None-Match") == self.etag:
                return self.respond(304)
            ctype, data = StaticHTMLContent[path]
            headers = {"ETag": self.etag}
            if self.can_deflate():
                try:
                    data = DeflatedStaticHTMLContent[path]
                    headers["Content-Encoding"] = "deflate"
                except KeyError:
                    pass
            return self.respond(200, ctype, data, headers=headers)

        method = getattr(self, "cmd_" + path, None)
        if method:
            method(params)
            if not self._response_sent:
                self.respond(200)
            return

        try:
            ListManager.quit(self.quitcmds[path.lower()])
            return self.respond(200)
        except KeyError:
            pass

        return self.respond(404)

    def respond(self, code, ctype=None, data=None, headers={}):
        self.send_response(code)
        if ctype:
            self.send_header("Content-Type", ctype)
        for k, v in headers.iteritems():
            self.send_header(k, v)
        self.end_headers()
        if data:
            self.wfile.write(data)
        self._response_sent = True

    def respond_with_list(self, data, headers={}):
        self.respond(200, "text/plain; charset=utf-8", '\n'.join(data), headers)

    def can_deflate(self):
        return ("deflate" in self.headers.get("Accept-Encoding", ""))

    def cmd_tracklist(self, params):
        etag = ListManager.scan_tag
        if not etag:
            return self.respond_with_list(ListManager.get_tracklist())
        if self.headers.get("If-None-Match") == etag:
            return self.respond(304)
        headers = {"ETag": etag}
        deflate = self.can_deflate()
        if deflate: headers["Content-Encoding"] = "deflate"
        self.respond(200, "text/plain; charset=utf-8", ListManager.get_tracklist_str(deflate), headers)

    def cmd_playlist(self, params):  self.respond_with_list(ListManager.get_playlist())
    def cmd_history(self, params):   self.respond_with_list(ListManager.get_history())
    def cmd_add(self, params):       ListManager.add_to_back(params)
    def cmd_insert(self, params):    ListManager.add_to_front(params)
    def cmd_playnow(self, params):   ListManager.play_specific(params)
    def cmd_remove(self, params):    ListManager.remove_file(params)
    def cmd_rollback(self, params):  ListManager.rewind_to(params)
    def cmd_prev(self, params):      ListManager.prev()
    def cmd_next(self, params):      ListManager.next()
    def cmd_play(self, params):      ListManager.play()
    def cmd_stop(self, params):      ListManager.stop()
    def cmd_rescan(self, params):    ListManager.rescan()

    def log_message(self, format, *args):
        log(format % args)

################################################################################

def quitcmd(s):
    try:
        cmd, code = map(str.strip, s.replace(':', '=').split('='))
    except ValueError:
        cmd = s.strip()
        code = 0
    return (cmd, int(code))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        usage="%(prog)s [OPTIONS...] [SRCDIR]",
        description=__doc__.strip(),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("srcdir", metavar="SRCDIR", nargs='?', default='.',
                        help="input directory")
    parser.add_argument("-V", "--version", action='version', version=__version__)
    parser.add_argument("-p", "--port", metavar="N", type=int, default=DefaultPort,
                        help="web interface port [default: %(default)s]")
    parser.add_argument("-x", "--player", metavar="EXE",
                        help="set player to use (%s) and optional parameters [default: autodetect]" % '/'.join(p.split()[0] for p in Players))
    parser.add_argument("-w", "--windowed", action='store_true',
                        help="do not run video player in fullscreen mode")
    parser.add_argument("-f", "--statefile", metavar="FILE", default=DefaultStateFile,
                        help="file to save state (history, playlist, play counts) to [default: %(default)s]")
    parser.add_argument("-a", "--autosave", action='store_true', default=ListManager.autosave,
                        help="save state file at every played track")
    parser.add_argument("-s", "--autoscan", action='store_true',
                        help="automatically rescan the input directory at every played track")
    parser.add_argument("-r", "--autoplay", action='store_true',
                        help="start playback immediately on initialization")
    parser.add_argument("-d", "--maxhist", metavar="N", type=int, default=DefaultHistoryDepth,
                        help="only preserve history for the last N tracks [default: %(default)s]")
    parser.add_argument("-t", "--logo", metavar="FILE",
                        help="display a text file instead of the IP address on the info screen ('-' to disable info screen logo completely)")
    parser.add_argument("-l", "--logfile", metavar="FILE",
                        help="produce debug logfile")
    parser.add_argument("-q", "--quitcmd", metavar="CMD[=EXITCODE]", type=quitcmd, action='append',
                        help="define web requests that cause the program to quit")
    args = parser.parse_args()

    ListManager.set_root(args.srcdir)
    ListManager.autoscan = args.autoscan
    ListManager.autosave = args.autosave
    ListManager.maxhist = args.maxhist
    WebRequestHandler.quitcmds = dict(args.quitcmd or [])

    if args.logfile:
        try:
            logfile = open(args.logfile, "a")
            print >>logfile
            print >>logfile, 79 * '='
            log("kjukebox %s starting" % __version__)
            print >>logfile, 79 * '='
            logfile.flush()
        except EnvironmentError, e:
            print >>sys.stderr, "ERROR: failed to open log file -", e
            sys.exit(1)

    ListManager.cmdline = setup_player(args.player, fullscreen=not(args.windowed))
    if not ListManager.cmdline:
        if args.player:
            parser.error("selected player %r is invalid or unavailable" % args.player)
        else:
            parser.error("could not find a player, use --player option to specify one manually")

    try:
        print "starting web server ..."
        httpd = WebServer(('', args.port), WebRequestHandler)
        httpd_thread = threading.Thread(target=httpd.serve_forever)
        httpd_thread.daemon = True
        mod_gzip()
        httpd_thread.start()
    except EnvironmentError, e:
        log("FATAL: can not start web server - %s" % e, True)
        sys.exit(1)

    print "server started at port", args.port
    if args.logo:
        StatusScreen.init(logofile=args.logo)
    else:
        ip = get_own_ip()
        if is_local_ip(ip):
            StatusScreen.init()
        else:
            stext = "http://\0%s" % get_own_ip()
            if args.port != 80:
                stext += "\0:%s" % args.port
            StatusScreen.init(text=stext)

    try:
        print "scanning for files ..."
        ListManager.rescan()
        ListManager.load_state(args.statefile)
        print "initial scan finished,", len(ListManager.files), "file(s) found."

        if args.autoplay:
            ListManager.play()
        else:
            StatusScreen.update()

        while ListManager.retcode is None:
            time.sleep(PollInterval)
            ListManager.tick()
    except KeyboardInterrupt:
        print " -- aborted by user, shutting down."

    log("kjukebox exiting")
    ListManager.stop()
    ListManager.save_state(sort=True)
    httpd.shutdown()
    httpd.server_close()
    log("kjukebox exited")
    if logfile:
        logfile.close()
    sys.exit(ListManager.retcode or 0)
