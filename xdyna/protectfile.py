"""
This package is an attempt to make file reading/writing (possibly concurrent) more reliable.

Last update 18/04/2022 - F.F. Van der Veken
"""

import io, shutil, time, pathlib, tempfile, datetime, atexit, hashlib
# import os, inspect, socket

tempdir = tempfile.TemporaryDirectory()
protected_open = {}

def exit_handler():
    """This handles cleaning of potential leftover lockfiles and backups."""
    for file in protected_open.values():
        file.release(pop=False)
    tempdir.cleanup()
atexit.register(exit_handler)

def get_hash(filename, size=128):
    """Get a fast hash of a file, in chunks of 'size' (in kb)"""
    h  = hashlib.blake2b()
    b  = bytearray(size*1024)
    mv = memoryview(b)
    with open(filename, 'rb', buffering=0) as f:
        for n in iter(lambda : f.readinto(mv), 0):
            h.update(mv[:n])
    return h.hexdigest()


class ProtectFile:
    """A wrapper around a file pointer, protecting it with a lockfile and backups.
    
    Use
    ---
    It is meant to be used inside a context, where the entering and leaving of a
    context ensures the file protection. The moment the object is instantiated, a
    lockfile is generated (which is destroyed after leaving the context). Attempts
    to access the file will be postponed as long as a lockfile exists. Furthermore,
    while in the context, file operations are done on a temporary file, that is
    only moved back when leaving the context.

    The reason to lock read access as well, is that we might work with immutable
    files. The following scenario might happen: a file is read by process 1, some
    calculations are done by process 1, the file is read by process 2, and the
    result of the calculations are written by process 1. Now process 2 is working
    on an outdated version of the file. Hence the full process should be locked in
    one go: reading, manipulating/calculating, writing.

    An important caveat is that, after the manipulation/calculation, the file
    contents have to be wiped before writing, otherwise the contents will be
    appended (as the file pointer is still at the end of the file after reading it
    in). Unless of course that is the intended result. Wiping the file can be
    achieved with the built-in truncate() and seek() methods.

    Attributes
    ----------
    file       : pathlib.Path
        The path to the file to be protected.
    lockfile   : pathlib.Path
        The path to the lockfile.
    tempfile   : pathlib.Path
        The path to a temporary file which will accumulate all writes until the
        ProtectFile object is destroyed, at which point the temporary file will
        replace the original file. Not used when a ProtectFile object is
        instantiated in read-only mode ('r' or 'rb').
    backupfile : pathlib.Path
        The path to a backup file in the same folder. This is to not lose the file
        in case of a catastrophic crash. This can be switched off by setting
        'backup_during_lock'=False. On the other hand, the option 'backup'=True
        will keep the backup file even after destroying the ProtectFile object. Not
        used when a ProtectFile object is instantiated in read-only mode ('r' or
        'rb'), unless 'backup_if_readonly'=True.
    
    Examples
    --------
    Reading in a file (while making sure it is not written to by another process):

    >>> from protectfile import ProtectedFile
    >>> with ProtectedFile(thebook.txt, 'r', backup=False, wait=1) as pf:
    >>>    text = pf.read()

    Reading and appending to a file:

    >>> from protectfile import ProtectedFile
    >>> with ProtectedFile(thebook.txt, 'r+', backup=False, wait=1) as pf:
    >>>    text = pf.read()
    >>>    pf.write("This string will be added at the end of the file, \
    ...               however, it won't be added to the 'text' variable")

    Reading and updating a JSON file:

    >>> import json
    >>> from protectfile import ProtectedFile
    >>> with ProtectedFile(info.json, 'r+', backup=False, wait=1) as pf:
    >>>     meta = json.load(pf)
    >>>     meta.update({'author': 'Emperor Claudius'})
    >>>     pf.truncate(0)          # Delete file contents (to avoid appending)
    >>>     pf.seek(0)              # Move file pointer to start of file
    >>>     json.dump(meta, pf, indent=2, sort_keys=False))

    Reading and updating a Parquet file:

    >>> import pandas as pd
    >>> from protectfile import ProtectedFile
    >>> with ProtectedFile(mydata.parquet, 'r+b', backup=False, wait=1) as pf:
    >>>     data = pd.read_parquet(pf)
    >>>     data['x'] += 5
    >>>     pf.truncate(0)          # Delete file contents (to avoid appending)
    >>>     pf.seek(0)              # Move file pointer to start of file
    >>>     data.to_parquet(pf, index=True)
    """
    
    def __init__(self, *args, **kwargs):
        """A ProtectFile object, to be used only in a context.
        
        Parameters
        ---------
        wait : int, default 1
            When a file is locked, the time to wait before trying to acess it again.
        backup_during_lock : bool, default True
            Whether or not to use a temporary backup file, to restore in case of
            failure.
        backup : bool, default False
            Whether or not to keep this backup file after the ProtectFile object
            is destroyed.
        backup_if_readonly : bool, default False
            Whether or not to use the backup mechanism when a file is in read-only
            mode ('r' or 'rb').
        check_hash : bool, default True
            Whether or not to verify by hash that the move of the temporary file to
            the original file succeeded.
        
        Additionally, the following parameters are inherited from open():
            'file', 'mode', 'buffering', 'encoding', 'errors', 'newline', 'closefd', 'opener'
        """
        
        argnames_open = ['file', 'mode', 'buffering', 'encoding', 'errors', 'newline', 'closefd', 'opener']
        arg = dict(zip(argnames_open, args))
        arg.update(kwargs)

        wait = arg.pop('wait', 1)
        # Backup during locking process (set to False for very big files)
        self._do_backup = arg.pop('backup_during_lock', True)
        # Keep backup even after unlocking
        self._keep_backup = arg.pop('backup', False)
        # If backup is to be kept, then it should be activated anyhow
        if self._keep_backup:
            self._do_backup = True
        self._backup_if_readonly = arg.pop('backup_if_readonly', False)
        self._check_hash = arg.pop('check_hash', True)

        # Initialise paths
        arg['file'] = pathlib.Path(arg['file']).resolve()
        file = arg['file']
        self._file = file
        self._lock = pathlib.Path(file.parent, file.name + '.lock').resolve()
        self._temp = pathlib.Path(tempdir.name, file.name).resolve()

        # Try to make lockfile, wait if unsuccesful
        while True:
            try:
                self._flock = io.open(self._lock, 'x')
#                 # TODO:  what follows is irrelevant as this is not written until file is closed,
#                 # which only happens at cleanup
#                 # Write info in lock for debugging
#                 locktext =  'Timestamp:  ' + datetime.datetime.now().isoformat() + '\n'
#                 locktext += 'Hostname:   ' + socket.gethostname() + '\n'
#                 locktext += 'Local IP:   ' + socket.gethostbyname(socket.gethostname()) + '\n'
#                 locktext += 'Process ID: ' + str(os.getpid()) + '\n\n'
#                 frameinfo = ['frame', 'filename', 'lineno', 'function', 'code_context', 'index']
#                 for i, st in enumerate(inspect.stack()):
#                     locktext += 'Stack ' + str(i) + ':' + os.linesep
#                     for j, fr in enumerate(st):
#                         locktext += frameinfo[j] + ': ' + str(fr) + os.linesep
#                     locktext += os.linesep
#                 self._flock.write(locktext)
                break
            except (IOError, OSError, FileExistsError):
                time.sleep(wait)

        # Clean up modes: we only use 'x' and 'r' (not 'w' and 'r') to have clear
        # flow on new vs existing files
        self._exists = True if file.is_file() else False
        mode = arg.get('mode','r')
        self._readonly = False
        if 'r' in mode:
            if not self._exists:
                raise FileNotFoundError
            if not '+' in mode:
                self._readonly = True
        elif 'x' in mode:
            if self._exists:
                raise FileExistsError
        else:
            if self._exists:
                arg['mode'] = arg['mode'].replace("+", "").replace("w", "r+").replace("a", "r+")
            else:
                arg['mode'] = arg['mode'].replace("w", "x").replace("a", "x")

        # Make a backup if requested
        if self._readonly and not self._backup_if_readonly:
            self._do_backup = False
        if self._do_backup and self._exists:
            self._backup = pathlib.Path(file.parent, file.name + '.backup').resolve()
            shutil.copy2(self._file, self._backup)
        else:
            self._backup = None

        # Store stats (to check if file got corrupted later)
        if self._exists:
            self._fstat = file.stat()

        # Choose file pointer:
        # Temporary if writing, or existing file if read-only
        if not self._readonly:
            if self._exists:
                shutil.copy2(self._file, self._temp)
            arg['file'] = self._temp        
        self._fd = io.open(**arg)

        # Store object in class dict for cleanup in case of sysexit
        protected_open[self._file] = self


    def __del__(self, *args, **kwargs):
        self.release()

    def __enter__(self, *args, **kwargs):
        return self._fd

    def __exit__(self, *args, **kwargs):
        # Close file pointer
        if not self._fd.closed:
            self._fd.close()
        # Check that original file was not modified in between (i.e. corrupted)
        # TODO: verify that checking file stats is 1) enough, and 2) not
        #       potentially problematic on certain file systems (i.e. if the
        #       system would periodically access the file, this would fail)
        if self._exists and (self.file.stat()!= self._fstat):
            print(f"Error: File {self.file} changed during lock!")
            # If corrupted, restore from backup
            # and move result of calculation (i.e. tempfile) to the parent folder
            self.restore()
        else:
            # All is fine: move result from temporary file to original
            self.mv_temp()
        self.release()

    def mv_temp(self, destination=None):
        """Move temporary file to 'destination' (the original file if destination=None)"""
        if not self._readonly:
            if destination is None:
                # Move temporary file to original file
                shutil.copy2(self._temp, self.file)
                # Check if copy succeeded
                if self._check_hash and get_hash(self._temp) != get_hash(self.file):
                    print(f"Warning: tried to copy temporary file {self._temp} into {self.file}, "
                          + "but hashes do not match!")
                    self.restore()
            else:
                shutil.copy2(self._temp, destination)
            self._temp.unlink()


    def restore(self):
        """Restore the original file from backup and save calculation results"""
        if self._do_backup:
            self._backup.rename(self.file)
            print('Restored file to previous state.')
        if not self._readonly:
            alt_file = pathlib.Path(self.file.parent, self.file.name + '__' \
                       + datetime.datetime.now().isoformat() + '.result').resolve()
            self.mv_temp(alt_file)
            print(f"Saved calculation results in {alt_file.name}.")


    def release(self, pop=True):
        """Clean up lockfile, tempfile, and backupfile"""
        # Overly verbose in checking, as to make sure this never fails (to avoid being stuck with remnant lockfiles)
        if hasattr(self,'_fd') and hasattr(self._fd,'closed') and not self._fd.closed:
            self._fd.close()
        if hasattr(self,'_temp') and hasattr(self._temp,'is_file') and self._temp.is_file():
            self._temp.unlink()
        if hasattr(self,'_do_backup') and hasattr(self,'_backup') and \
                hasattr(self._backup,'is_file') and hasattr(self,'_keep_backup') and \
                self._do_backup and self._backup.is_file() and not self._keep_backup:
            self._backup.unlink()
        if hasattr(self,'_flock') and hasattr(self._flock,'closed') and not self._flock.closed:
            self._flock.close()
        if hasattr(self,'_lock') and hasattr(self._lock,'is_file') and self._lock.is_file():
            self._lock.unlink()
        if pop:
            protected_open.pop(self._file, 0)


    @property
    def file(self):
        return self._file

    @property
    def lockfile(self):
        return self._lock

    @property
    def tempfile(self):
        return self._temp

    @property
    def backupfile(self):
        return self._backup

