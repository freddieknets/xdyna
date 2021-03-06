import numbers
from pathlib import Path
import json

from .protectfile import ProtectFile

# Developers: if new metadata is added, the following steps have to be implemented:
#    - description in docstring
#    - initialisation above and in __init__
#    - @property setter and getter
#    - initialisation in regenerate_da_metadata()
#    - added to _cols
#    - potential initialisation in DA

# TODO: missing particle selection ...

def regenerate_da_metadata(filename, *, da_type=None, da_dim=None, emitx=None, emity=None, turns=0, energy=0,
                           nseeds=0, pairs_shift=0, pairs_shift_var=None, s_start=None, submissions={}):
    """Function to manually regenerate the *.meta.json file, in case it got corrupted or deleted.
    
    """
    meta = _DAMetaData(filename, skip_file_generation=True)
    if meta.meta_file.exists():
        print("Warning: metadata file already exists! Not regenerated.")
    else:
        meta._da_type = da_type
        meta._da_dim = da_dim
        meta._emitx = emitx
        meta._emity = emity
        meta._turns = turns
        meta._energy = energy
        meta._nseeds = nseeds
        meta._pairs_shift = pairs_shift
        meta._pairs_shift_var = pairs_shift_var
        meta._s_start = s_start
        meta._submissions = submissions
        meta._store()
        return meta


class _DAMetaData:
    """Collects all info of a DA study, and keeps the .meta.json file in sync.
    
    Attributes
    ----------
    name : str
        Name of the study. All files will have this name as stem.
    path : pathlib.Path
        Path to the study folder. Different studies are allowed in the same
        folder, as long as they have different names.
    da_type : str
        The type of DA, representing how the initial conditions are
        generated. Possible types are:
        radial      : a polar grid , expressed by one amplitude and
                      (potentially multiple) angles.
        grid        : a rectangular grid.
        monte_carlo : random sampling, where the DA border region is
                      recognised by an ML model.
        free        : any form of initial conditions, where the DA border
                      region is recognised by an ML model.
    da_dim : int
        The dimension of the DA. This is the number of dimensions over
        which the initial conditions are varied.
    emitx : float
        The horizontal emittance used to calculated the beam size which
        is used to calculate the initial conditions.
    emity : float
        The vertical emittance used to calculated the beam size which
        is used to calculate the initial conditions.
    turns : int
        The maximum number of turns to track.
    energy : float
        The energy of the beam.
    nseeds : int
        The number of seeds used in the DA. If no seeds are used, this
        is equal to zero.
    pairs_shift : float
        The separation between two particles in a pair. If no pairs are
        used, this is equal to zero.
    pairs_shift_var : str
        The variable upon which to make the shift between pairs. This can
        be 'angle', 'r', 'x', 'y', 'px', 'py', 'zeta', or 'delta'.
    s_start : float
        The longitudinal position of the initial conditions in the lattice.
    meta_file : pathlib.Path
        Path to the metadata file (*.meta.json)
    line_file : pathlib.Path
        Path to the xtrack line file (*.line.json)
    six_path : pathlib.Path
        Path to the sixtrack input (six_path/(seed)/fort.*)
    surv_file : pathlib.Path
        Path to the survival file (*.surv.parquet)
    da_file : pathlib.Path
        Path to the da file (*.da.parquet)
    da_evol_file : pathlib.Path
        Path to the da evolution file (*.da_evol.parquet)
    submissions : list
        A log with info about the submitted jobs. A new job ID can be
        generated with new_submission_id, and the log can be updated with
        update_submissions.
    """

    # Class Attributes
    # ----------------
    #
    # _cols is (only) used to define the order of fields in the json
    # _path_cols is used to list those that require a special treatment:
    #      They need to have a .to_posix() call before storing in the json
    # _auto_cols are calculated automatically and do not need to be read in
    # _optional_cols will not be stored to the json if their value is None
    
    _cols = ['name','path','da_type','da_dim','emitx','emity','turns','energy','nseeds','pairs_shift','pairs_shift_var',\
             's_start','meta_file','line_file','six_path','surv_file','da_file','da_evol_file','submissions']
    _path_cols = ['path','meta_file','line_file','six_path','surv_file','da_file','da_evol_file']
    _auto_cols = ['name','path','meta_file','surv_file','da_file','da_evol_file']
    _optional_cols = ['six_path','line_file']
    # used to specify the accepted DA types
    _da_types=['radial', 'grid', 'monte_carlo', 'free']

    _da_type_default         = None
    _da_dim_default          = None
    _emitx_default           = None
    _emity_default           = None
    _turns_default           = 0
    _energy_default          = 0
    _nseeds_default          = 0
    _pairs_shift_default     = 0
    _pairs_shift_var_default = None
    _s_start_default         = 0
    _submissions_default     = {}


    def __init__(self, *, filename, skip_file_generation=False):
        self._filename = Path(filename).resolve()
        if self._filename.suffixes[-2:] == ['.meta', '.json']:
            # Remove .meta.json suffix if passed with filename
            self._filename = Path(self._filename.parent, '.'.join(self._filename.name.split('.')[:-2]))
        self._da_type         = self._da_type_default
        self._da_dim          = self._da_dim_default
        self._emitx           = self._emitx_default
        self._emity           = self._emity_default
        self._turns           = self._turns_default
        self._energy          = self._energy_default
        self._nseeds          = self._nseeds_default
        self._pairs_shift     = self._pairs_shift_default
        self._pairs_shift_var = self._pairs_shift_var_default
        self._s_start         = self._s_start_default
        self._submissions     = self._submissions_default
        self._six_path        = None
        self._line_file        = None
        if not skip_file_generation:
            if self.meta_file.exists():
                print("Loading existing DA object.")
                self._read()
                # Store again, to update paths if needed
                self._store()
            else:
                if self.surv_file.exists() or self.da_file.exists() or self.da_evol_file.exists():
                     raise ValueError("Tried to create new DA object, but some parquet files already exist!\n" \
                                     + "If you tried to load an existing DA object, make sure to keep the *.meta.json " \
                                     + "file in the same folder as the parquet files, or regenerate the metadata file " \
                                     + "manually with xdyna.regenerate_da_metadata(). Or, if the parquet files are old/wrong, " \
                                     + "just delete them.")
                print("Creating new DA object.")
                self._store()


    @property
    def name(self):
        return self._filename.name

    @property
    def path(self):
        return self._filename.parent

    @property
    def meta_file(self):
        return Path(self.path, self.name + '.meta.json').resolve()

    @property
    def line_file(self):
        return self._line_file

    @line_file.setter
    def line_file(self, line_file):
        line_file = Path(line_file).resolve()
        self._set_property('line_file', line_file)

    @property
    def six_path(self):
        return self._six_path

    @six_path.setter
    def six_path(self, six_path):
        six_path = Path(six_path).resolve()
        if not six_path.exists():
            raise ValueError(f"The path {six_path} does not exist!")
        self._set_property('six_path', six_path)

    @property
    def surv_file(self):
        return Path(self.path, self.name + '.surv.parquet').resolve()

    @property
    def da_file(self):
        return Path(self.path, self.name + '.da.parquet').resolve()

    @property
    def da_evol_file(self):
        return Path(self.path, self.name + '.da_evol.parquet').resolve()

    @property
    def da_type(self):
        return self._da_type

    @da_type.setter
    def da_type(self, da_type):
        if not da_type in self._da_types:
            raise ValueError(f"The variable da_dim should be one of {', '.join(self._da_types)}!")
        self._set_property('da_type', da_type)

    @property
    def da_dim(self):
        return self._da_dim

    @da_dim.setter
    def da_dim(self, da_dim):
        if not isinstance(da_dim, numbers.Number) or da_dim < 2 or da_dim > 6:
            raise ValueError(f"The variable da_dim should be a number between 2 and 6!")
        self._set_property('da_dim', round(da_dim))

    @property
    def emitx(self):
        return self._emitx

    @emitx.setter
    def emitx(self, emitx):
        if not isinstance(emitx, numbers.Number):
            raise ValueError(f"The emittance should be a number!")
        if emitx <= 0:
            raise ValueError(f"The emittance has to be larger than zero!")
        self._set_property('emitx', emitx)

    @property
    def emity(self):
        return self._emity

    @emity.setter
    def emity(self, emity):
        if not isinstance(emity, numbers.Number):
            raise ValueError(f"The emittance should be a number!")
        if emity <= 0:
            raise ValueError(f"The emittance has to be larger than zero!")
        self._set_property('emity', emity)

    @property
    def turns(self):
        return self._turns

    @turns.setter
    def turns(self, turns):
        if not isinstance(turns, numbers.Number):
            raise ValueError(f"The number of turns should be a number!")
        self._set_property('turns', round(turns))

    @property
    def energy(self):
        return self._energy

    @energy.setter
    def energy(self, energy):
        if not isinstance(energy, numbers.Number):
            raise ValueError(f"The energy should be a number!")
        if energy <= 0:
            raise ValueError(f"The energy has to be larger than zero!")
        self._set_property('energy', energy)

    @property
    def nseeds(self):
        return self._nseeds

    @nseeds.setter
    def nseeds(self, nseeds):
        if not isinstance(nseeds, numbers.Number):
            raise ValueError(f"The number of seeds should be a number!")
        self._set_property('nseeds', round(nseeds))

    @property
    def pairs_shift(self):
        return self._pairs_shift

    @pairs_shift.setter
    def pairs_shift(self, pairs_shift):
        if not isinstance(pairs_shift, numbers.Number):
            raise ValueError(f"The variable pairs_shift should be a number!")
        self._set_property('pairs_shift', pairs_shift)

    @property
    def pairs_shift_var(self):
        return self._pairs_shift_var

    @pairs_shift_var.setter
    def pairs_shift_var(self, pairs_shift_var):
        accepted = ['r', 'angle', 'x', 'y', 'px', 'py', 'zeta', 'delta']
        if pairs_shift_var not in accepted:
            raise ValueError(f"The variable pairs_shift_var should be one of {', '.join(accepted)}!")
        self._set_property('pairs_shift_var', pairs_shift_var)

    @property
    def s_start(self):
        return self._s_start

    @s_start.setter
    def s_start(self, s_start):
        if not isinstance(s_start, numbers.Number):
            raise ValueError(f"The variable s_start should be a number!")
        self._set_property('s_start', s_start)

    @property
    def submissions(self):
        return self._submissions

    # Allowed on parallel process
    def new_submission_id(self):
        with ProtectFile(self.meta_file, 'r+', wait=0.005) as pf:
            meta = json.load(pf)
            new_id = len(meta['submissions'].keys())
            meta['submissions'][new_id] = None
            pf.truncate(0)  # Delete file contents (to avoid appending)
            pf.seek(0)      # Move file pointer to start of file
            json.dump(meta, pf, indent=2, sort_keys=False)
            self._submissions = meta
            return new_id

    # Allowed on parallel process
    def update_submissions(self, submission_id, val):
        with ProtectFile(self.meta_file, 'r+', wait=0.005) as pf:
            meta = json.load(pf)
            meta['submissions'].update({submission_id: val})
            pf.truncate(0)  # Delete file contents (to avoid appending)
            pf.seek(0)      # Move file pointer to start of file
            json.dump(meta, pf, indent=2, sort_keys=False)
            self._submissions = meta

    def _set_property(self, prop, val):
        if getattr(self, '_' + prop) != val:
            setattr(self, '_' + prop, val)
            self._check_not_changed(ignore=[prop])
            self._store()

    # TODO: is this superfluous?
    def _check_not_changed(self, ignore=[]):
        # Create dict of self fields, ignoring the field that is expected to change
        # Also ignore optional keys that are not set
        ignore += [ x for x in self._optional_cols if getattr(self, x) is None ]
        sortkeys = [ x for x in self._cols if x not in ignore ]
        thisdict = { key: getattr(self, key) for key in sortkeys }
        # Special treatment for paths: make them strings
        self._paths_to_strings(thisdict, ignore)
        # Load file
        with ProtectFile(self.meta_file, 'r') as pf:
            meta = json.load(pf)
        meta = { key: meta[key] for key in sortkeys }
        # Compare
        if meta != thisdict:
            raise Exception("The metadata file changed on disk!\n" \
                           + "This is not supposed to happen, and probably means that one of the child processes " \
                           + "tried to write to it (which is only allowed for the 'submissions' field).\n" \
                           + "Please check your workflow.")

    def _read(self):
        # Do not read _auto_cols; these are calculated automatically.
        with ProtectFile(self.meta_file, 'r') as pf:
            meta = json.load(pf)
            for key in [ x for x in self._cols if x not in self._auto_cols ]:
                # Default to None, in case of optional keys
                val = meta.get(key, None)
                if key in self._path_cols and val is not None:
                    val = Path(val)
                setattr(self, '_' + key, val )

    def _store(self):
        # Store everything except  the optional keys that are None
        ignore = [ x for x in self._optional_cols if getattr(self, x) is None ]
        sortkeys = [ x for x in self._cols if x not in ignore ]
        meta = { key: getattr(self, key) for key in sortkeys }
        self._paths_to_strings(meta, ignore)
        mode = 'r+' if self.meta_file.exists() else 'x+'
        with ProtectFile(self.meta_file, mode) as pf:
            if mode == 'r+':
                pf.truncate(0)  # Delete file contents (to avoid appending)
                pf.seek(0)      # Move file pointer to start of file
            json.dump(meta, pf, indent=2, sort_keys=False)
    
    def _paths_to_strings(self, meta, ignore=[]):
        meta.update({key: getattr(self,key).as_posix() for key in self._path_cols if key not in ignore})
