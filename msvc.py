import os
import subprocess
from dataclasses import dataclass, field
from functools import cache, cached_property
from enum import Enum
from queue import SimpleQueue
from time import time
from argparse import ArgumentParser
from sys import argv

class bcolors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

@dataclass
class Timer:
    elapsed: float = None
    _start: float = None 
    _end: float = None

    def __enter__(self):
        self._start = time()
    
    def __exit__(self, t, v, tb):
        self._end = time()
        self.elapsed = self._end - self._start

def _Parse_Target(args: tuple[str] = None) -> str:
    parser = ArgumentParser(args[0] if args else argv[0], description='MSVC-based C++latest project management')
    parser.add_argument('target', type=str, choices=['build', 'rebuild', 'clean', 'test'])
    args = parser.parse_args(args=args)
    return args.target


class CompilationError(RuntimeError):
    pass

def _Basename_Ext(path: os.PathLike, ext: str) -> os.PathLike:
    return f'{os.path.basename(path)}{ext}' 

def _Path_Join(*parts: os.PathLike) -> os.PathLike:
    return os.path.normpath(os.path.join(*parts))

def _Path_Dir(path: os.PathLike) -> os.PathLike:
    return os.path.normpath(os.path.dirname(os.path.realpath(path)))

def _Dot_Path(path: os.PathLike, add_ext: str = None, strip_ext: bool = False) -> str:
    if strip_ext:
        path = os.path.splitext(path)[0]
    if add_ext:
        path += add_ext
    parts = os.path.normpath(path).split(os.sep)
    return '.'.join((p for p in parts if p))

def _Should_Rebuild(src: os.PathLike, dst: os.PathLike) -> bool:
    assert os.path.exists(src), f'source file {src} does not exist'
    return (not os.path.exists(dst)) or (os.path.getmtime(src) > os.path.getmtime(dst))

def _Shell_Exec(*args: str):
    cmd = ' '.join(args)
    print(f':MSVC> {cmd}')
    r = subprocess.run(cmd, shell=True, capture_output=True)
    
    stdout = r.stdout.decode('utf-8').strip()
    if stdout.startswith('Microsoft (R)'):
        stdout = '\n'.join( stdout.splitlines()[2:] )

    if stdout:
        print('  [stdout]', stdout)
        # print('---')
        if 'error C' in stdout:
            raise CompilationError()
    #if r.stderr:
    #    print('    stderr:', r.stderr.decode('utf-8'))

class _Msvc_Tool(Enum):
    Compiler = 'CL.EXE'
    Linker = 'LINK.EXE'
    LibMgr = 'LIB.EXE'

class _CFlag(Enum):
    ExcMode = '/EHsc'
    Standard = '/std:c++latest'
    Debug = '/Zi'
    WAll = '/Wall' # me am a psycho schizo masochist
    W3 = '/W3'
    W2 = '/W2'
    WError = '/WX'
    ObjPath = '/Fo'
    PdbPath = '/Fd'
    ExePath = '/Fe'
    Linkless = '/c'
    DisableOptimizations = '/Od'
    InlineFunctionsExpansion = '/Ob2'
    WholeProgramOptimization = '/GL'
    LinkTimeCodeGeneration = '/LTCG'

class _LFlag(Enum):
    WError = '/WX'
    Debug = '/DEBUG:FULL'

class _IfcFlag(Enum):
    TranslationUnit = '/TP'
    Interface = '/interface'
    Partition = '/internalPartition' # not using module partitions (yet?) 'cause they are retarded af
    ExportLocalHeaderUnit = '/exportHeader /headerName:quote'
    ExportGlobalHeaderUnit = '/exportHeader /headerName:angle'
    IncludeLocalHeaderUnit = '/headerUnit:quote'
    IncludeGlobalHeaderUnit = '/headerUnit:angle'
    IfcSearchDir = '/ifcSearchDir'
    IfcOutput = '/ifcOutput'
    IfcMap = '/ifcMap'

class ConfigType(Enum):
    Debug = 'Debug'
    Release = 'Release'
    Custom = 'Custom'

def _Unwrap_Value(x) -> str:
    if isinstance(x, Enum):
        return x.value
    return str(x)

@dataclass
class Config:
    type: ConfigType
    cflags: list[_CFlag] = field(default_factory=list)
    lflags: list[_LFlag] = field(default_factory=list)
    
    def __post_init__(self):
        match self.type:
            case ConfigType.Debug:
                self.cflags = Config._DebugCFlags.copy()
                self.lflags = Config._DebugLFlags.copy()
            case ConfigType.Release:
                self.cflags = Config._ReleaseCFlags.copy()
                self.lflags = Config._ReleaseLFlags.copy()
            case _:
                pass

    @cached_property
    def compiler_args(self) -> list[str]:
        return [ _Unwrap_Value(x) for x in self.cflags ]
    
    @cached_property
    def linker_args(self) -> list[str]:
        return [ _Unwrap_Value(x) for x in self.lflags ]
    
    def link(self, libraries: list[os.PathLike]):
        if libraries:
            self.lflags += libraries
        return self

    _DebugCFlags = [ _CFlag.ExcMode, _CFlag.Standard, _CFlag.WAll, _CFlag.WError, 
                    _CFlag.Debug, _CFlag.DisableOptimizations ]
    _DebugLFlags = [ _LFlag.WError, _LFlag.Debug ]
    _ReleaseCFlags = [ _CFlag.ExcMode, _CFlag.Standard, _CFlag.WAll, _CFlag.WError, 
                    _CFlag.InlineFunctionsExpansion, _CFlag.WholeProgramOptimization ]
    _ReleaseLFlags = [ _LFlag.WError, _CFlag.LinkTimeCodeGeneration ]

class ProjectType(Enum):
    LIB = '.lib'
    EXE = '.exe'
    DLL = '.dll'

@dataclass
class ObjectAccumulator:
    # key: obj file, value: src file
    compiled: dict[os.PathLike, os.PathLike] = field(default_factory=dict)
    included: list[os.PathLike] = field(default_factory=list)

    def on_compile(self, src: os.PathLike, obj: os.PathLike) -> str:
        assert not obj in self.compiled, f'object file {obj} already exists; src is {src}, previously {self.compiled[obj]}'
        self.compiled[obj] = src
        self.included.append(obj)
        return f'{_CFlag.ObjPath.value}{obj}'


@dataclass 
class HeaderUnitAccumulator:
    exported: dict[str, os.PathLike] = field(default_factory=dict)
    included: list[str] = field(default_factory=list)

    def on_export(self, hxx: os.PathLike, ifc: os.PathLike) -> list[str]:
        assert not hxx in self.exported, f'header unit {hxx} is already exported'
        self.exported[hxx] = ifc
        kv = f'{hxx}={ifc}'
        self.included.append(_IfcFlag.IncludeGlobalHeaderUnit.value)
        self.included.append(kv)
        return [ _IfcFlag.ExportGlobalHeaderUnit.value, hxx ]

@dataclass
class ModuleAccumulator:
    exported: dict[str, os.PathLike] = field(default_factory=dict)

    def on_interface(self, ixx: os.PathLike, name: str, ifc: os.PathLike) -> list[str]:
        assert not name in self.exported, f'module {name} already exists'
        self.exported[name] = ifc
        return [ _IfcFlag.Interface.value, ixx ]


# TODO: batch pass multiple /TP ${cpp} files to CL.EXE in a single call
# TODO: batch pass multiple ${cxx} files to CL.EXE in a single call
@dataclass
class TranslationUnitAccumulator:
    scheduled: dict[os.PathLike, os.PathLike] = field(default_factory=dict)
    
    def on_schedule(self, cpp: os.PathLike, obj: os.PathLike):
        pass

@dataclass
class IfcMapAccumulator:
    external: dict[str, os.PathLike] = field(default_factory=dict)

    @cached_property
    def compiler_args(self):
        r = []
        for ifc_map in self.external.values():
            r.append(_IfcFlag.IfcMap.value)
            r.append(ifc_map)
        return r

_TOML_MODULE_TEMPLATE="""[[module]]
name = '%s'
ifc = '%s'

"""
_TOML_HEADER_UNIT_TEMPLATE="""[[header-unit]]
name = ['angle', '%s']
ifc = '%s'

"""

@dataclass 
class Project:
    name: str
    type: ProjectType
    source_directory: os.PathLike
    tests_directory: os.PathLike
    build_directory: os.PathLike
    config: Config
    header_units: HeaderUnitAccumulator = field(default_factory=HeaderUnitAccumulator)
    modules: ModuleAccumulator = field(default_factory=ModuleAccumulator)
    object_files: ObjectAccumulator = field(default_factory=ObjectAccumulator)
    deferred_commands: SimpleQueue[list[str]] = field(default_factory=SimpleQueue)
    ifc_maps: IfcMapAccumulator = field(default_factory=IfcMapAccumulator)

    _modules_directory: os.PathLike = None
    _cache_directory: os.PathLike = None
    _rebuilt_objects: int = 0
    _total_objects: int = 0
    _force_rebuild: bool = False
    

    def __post_init__(self):
        self.source_directory = os.path.normpath(self.source_directory)
        self.build_directory = os.path.normpath(self.build_directory)
        self.tests_directory = os.path.normpath(self.tests_directory)

        self._modules_directory = os.path.normpath(os.path.join(self.build_directory, 'ifc'))
        os.makedirs(self._modules_directory, exist_ok=True)
        
        self._cache_directory = os.path.normpath(os.path.join(self.build_directory, 'obj'))
        os.makedirs(self._cache_directory, exist_ok=True)

    @cached_property
    def output_file(self):
        return os.path.normpath(f'{self.build_directory}/{self.name}{self.type.value}')

    @cached_property
    def pdb_file(self):
        return os.path.normpath(f'{self.build_directory}/{self.name}.pdb')

    @cached_property
    def output_file_flag(self):
        return f'{_CFlag.ExePath.value}{self.output_file}'

    @cached_property 
    def pdb_file_flag(self):
        return f'{_CFlag.PdbPath.value}{self.pdb_file}'
    
    @cached_property
    def common_flags(self):
        return [
            '/I', self.source_directory
        ]

    @cached_property
    def ifc_search_dir(self):
        return [ _IfcFlag.IfcSearchDir.value, self._modules_directory ]

    def add_c_translation_unit(self, c: os.PathLike):
        assert os.path.splitext(c)[1] == '.c', f'file extension mismatch: expected .c, got {c}'

        obj = _Path_Join(self._cache_directory, _Dot_Path(c, add_ext='.obj'))
        c = _Path_Join(self.source_directory, c)

        cmd = [ _Msvc_Tool.Compiler.value, _CFlag.Linkless.value ]
        cmd.append('/std:c17')
        if self.config.type == ConfigType.Debug:
            cmd.append('/Zi')
        cmd.append('/I')
        cmd.append(self.source_directory)
        cmd.append(c)
        cmd.append(self.object_files.on_compile(c, obj))
        cmd.append(self.pdb_file_flag)
        cmd.append(self.output_file_flag)

        if not self._force_rebuild and _Should_Rebuild(c, obj):
            self.deferred_commands.put(cmd)
            self._rebuilt_objects += 1
        else:
            print(f':> Not building {os.path.relpath(c, self.source_directory)} (no changes)')
        # _Shell_Exec(*cmd)
        self._total_objects += 1
        return self

    def add_header_unit(self, hxx: os.PathLike):
        assert os.path.splitext(hxx)[1] == '.hxx', f'file extension mismatch: expected .hxx, got {hxx}'

        ifc = os.path.join(self._modules_directory, _Dot_Path(hxx, add_ext='.ifc'))
        obj = os.path.join(self._cache_directory, _Dot_Path(hxx, add_ext='.obj', strip_ext=True))
        hxx = os.path.normpath(hxx)

        cmd = [ _Msvc_Tool.Compiler.value, _CFlag.Linkless.value ]
        cmd += self.config.compiler_args
        cmd += self.common_flags
        cmd += self.ifc_search_dir
        cmd += self.header_units.on_export(hxx, ifc)
        cmd += self.header_units.included
        cmd += self.ifc_maps.compiler_args
        cmd += [ _IfcFlag.IfcOutput.value, ifc ]
        cmd.append(self.object_files.on_compile(hxx, obj))
        cmd.append(self.pdb_file_flag)
        cmd.append(self.output_file_flag)

        assert not hxx.startswith('C:\\')
        if not self._force_rebuild and _Should_Rebuild(os.path.join(self.source_directory, hxx), obj):
            _Shell_Exec(*cmd)
            self._rebuilt_objects += 1
        else:
            print(f':> Not building {hxx} (no changes)')
        self._total_objects += 1
        return self

    def add_module_interface(self, ixx: os.PathLike):
        assert os.path.splitext(ixx)[1] == '.ixx', f'file extension mismatch: expected .ixx, got {ixx}'

        name = _Dot_Path(ixx, strip_ext=True)
        ifc = _Path_Join(self._modules_directory, _Dot_Path(ixx, add_ext='.ifc', strip_ext=True))
        obj = _Path_Join(self._cache_directory, _Dot_Path(ixx, add_ext='.obj'))
        ixx = _Path_Join(self.source_directory, ixx)

        cmd = [ _Msvc_Tool.Compiler.value, _CFlag.Linkless.value ]
        cmd += self.config.compiler_args
        cmd += self.common_flags
        cmd += self.ifc_search_dir
        cmd += self.header_units.included
        cmd += self.ifc_maps.compiler_args
        cmd += self.modules.on_interface(ixx, name, ifc)
        cmd += [ _IfcFlag.IfcOutput.value, ifc ]
        cmd.append(self.object_files.on_compile(ixx, obj))
        cmd.append(self.pdb_file_flag)
        cmd.append(self.output_file_flag)

        if not self._force_rebuild and _Should_Rebuild(ixx, obj):
            _Shell_Exec(*cmd)
            self._rebuilt_objects += 1
        else:
            print(f':> Not building {os.path.relpath(ixx, self.source_directory)} (no changes)')
        self._total_objects += 1
        return self

    def add_module_implementation(self, cxx: os.PathLike):
        assert os.path.splitext(cxx)[1] == '.cxx', f'file extension mismatch: expected .cxx, got {cxx}'

        obj = _Path_Join(self._cache_directory, _Dot_Path(cxx, add_ext='.obj'))
        cxx = _Path_Join(self.source_directory, cxx)
        #obj = os.path.join(self._cache_directory, _Basename_Ext(cxx, '.obj'))
        
        cmd = [ _Msvc_Tool.Compiler.value, _CFlag.Linkless.value ]
        cmd += self.config.compiler_args
        cmd += self.common_flags
        cmd += self.ifc_search_dir
        cmd += self.header_units.included
        cmd += self.ifc_maps.compiler_args
        cmd.append(cxx)
        cmd.append(self.object_files.on_compile(cxx, obj))
        cmd.append(self.pdb_file_flag)
        cmd.append(self.output_file_flag)

        if not self._force_rebuild and _Should_Rebuild(cxx, obj):
            self.deferred_commands.put(cmd)
            self._rebuilt_objects += 1
        else:
            print(f':> Not building {os.path.relpath(cxx, self.source_directory)} (no changes)')
        # _Shell_Exec(*cmd)
        self._total_objects += 1
        return self
        
    def add_translation_unit(self, cpp: os.PathLike):
        assert os.path.splitext(cpp)[1] == '.cpp', f'file extension mismatch: expected .cpp, got {cpp}'

        obj = _Path_Join(self._cache_directory, _Dot_Path(cpp, add_ext='.obj'))
        cpp = _Path_Join(self.source_directory, cpp)
        
        cmd = [ _Msvc_Tool.Compiler.value, _CFlag.Linkless.value ]
        cmd += self.config.compiler_args
        cmd += self.common_flags
        cmd += self.ifc_search_dir
        cmd += self.header_units.included
        cmd += self.ifc_maps.compiler_args
        cmd.append(_IfcFlag.TranslationUnit.value)
        cmd.append(cpp)
        cmd.append(self.object_files.on_compile(cpp, obj))
        cmd.append(self.pdb_file_flag)
        cmd.append(self.output_file_flag)

        if not self._force_rebuild and _Should_Rebuild(cpp, obj):
            self.deferred_commands.put(cmd)
            self._rebuilt_objects += 1
        else:
            print(f':> Not building {os.path.relpath(cpp, self.source_directory)} (no changes)')
        # _Shell_Exec(*cmd)
        self._total_objects += 1
        return self
    
    def add_sources(self, sources: list[os.PathLike]):
        for source in sources:
            ext = os.path.splitext(source)[1]
            match ext:
                case '.hxx':
                    self.add_header_unit(source)
                case '.ixx':
                    self.add_module_interface(source)
                case '.cxx':
                    self.add_module_implementation(source)
                case '.cpp':
                    self.add_translation_unit(source)
                case '.c':
                    self.add_c_translation_unit(source)
                case _:
                    raise RuntimeError(f'Unsupported source type {ext}: {source}')
        return self

    def clean(self):
        from shutil import rmtree
        src = os.path.realpath(self.source_directory)
        dst = os.path.realpath(self.build_directory)
        assert src != dst and (dst.startswith(src) or not src.startswith(dst))
        rmtree(self.build_directory)
        os.makedirs(self._modules_directory, exist_ok=True)
        os.makedirs(self._cache_directory, exist_ok=True)
        print(f':> Cleaned {self.name}')

    def rebuild(self, *args, **kwargs):
        self._force_rebuild = True
        self.clean()
        return self.build(*args, **kwargs) 

    def build(self, sources: list[os.PathLike] = None):
        print(f'PROJECT {self.name}')

        _timer = Timer()
        with _timer:
            print(f':BUILD> {os.path.basename(self.output_file)}')
            if sources:
                self.add_sources(sources)
            
            if not self._force_rebuild and self._rebuilt_objects == 0:
                print(f':> Not linking {os.path.basename(self.output_file)} (no changes).')
            else:
                # compile deferred MImpls and TUnits
                # naive try on resolving circular dependencies 
                # by compiling all interfaces and header units first
                while not self.deferred_commands.empty():
                    cmd = self.deferred_commands.get()
                    _Shell_Exec(*cmd)

                print(f':> Rebuilt {self._rebuilt_objects}/{self._total_objects} source files.')

                match self.type:
                    case ProjectType.LIB:
                        self._build_lib()
                    case ProjectType.EXE:
                        self._build_exe()
                    case ProjectType.DLL:
                        self._build_dll()
                    case _:
                        raise ValueError('unsupported ProjectType')

                if self.modules.exported or self.header_units.exported:
                    self.generate_ifc_map()
                print(f':BUILT> {os.path.basename(self.output_file)}')
                print('---')

            self._build_tests()

        print(f'PROJECT {self.name} was built in {_timer.elapsed:.3f}s')
        print()

    def test(self, verbose: bool = True):
        self._build_tests()

        tests = [ 
            _Path_Join(self.build_directory, f) for f in os.listdir(self.build_directory) 
            if os.path.basename(f).startswith('test_') and f.endswith('.exe')
        ]
        try:
            for test in tests:
                testname = os.path.splitext(os.path.basename(test))[0]

                print(f'TEST {self.name}::{testname}')
                
                p = subprocess.Popen(args=tuple(), executable=test, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                outiter = iter(p.stdout.readline, b'')
                erriter = iter(p.stderr.readline, b'')
                
                while True:
                    outline = ''
                    errline = ''

                    outstop = False
                    try:
                        outline = next(outiter).decode().rstrip()
                    except StopIteration:
                        outstop = True

                    errstop = False
                    try:
                        errline = next(erriter).decode().rstrip()
                    except StopIteration:
                        errstop = True
                    
                    if outstop and errstop:
                        break
                    if verbose and outline:
                        print(':stdout>', outline)
                    if errline:
                        print(bcolors.FAIL + ':stderr> ' + errline + bcolors.ENDC)

                retcode = p.wait()
                if retcode != 0:
                    print(':exitcode>', retcode)
                    raise RuntimeError(f'TEST {self.name}::{testname}: FAILED')
                else:
                    print(':exitcode> 0')
                    print(f'TEST {self.name}::{testname}: SUCCESS')
                    print()

        except RuntimeError as e:
            print(str(e))
            print()
            return
        
    def on_target(self, target: str, *args, **kwargs):
        match target:
            case 'clean':
                self.clean()
            case 'rebuild':
                self.rebuild(*args, **kwargs)
            case 'build':
                #self.build(*args, **kwargs)
                # TODO: correctly rebuild dependencies /<= have to analyze them first :O?
                self.rebuild(*args, **kwargs)
            case 'test':
                self.test()
            case _:
                raise RuntimeError(f'Unsupported build target {target}')

    def _build_lib(self):
        cmd = [ _Msvc_Tool.LibMgr.value, f'/OUT:{self.output_file}' ]
        cmd += self.object_files.included
        
        _Shell_Exec(*cmd)

    def _build_exe(self):
        pass

    def _build_dll(self):
        pass

    def _build_tests(self):
        if not (self.tests_directory and os.path.exists(self.tests_directory)):
            return
         
        for test in os.listdir(self.tests_directory):
            testname, ext = os.path.splitext(os.path.basename(test))
            if (not testname.startswith('test_')) or (ext != '.uxx'):
                print(f":> Skipping {testname}{ext} from tests directory")
                continue
            
            if testname.endswith('.hidden'):
                print(f':> Skipping {testname}{ext} from tests directory (hidden)')
                continue

            uxx = _Path_Join(self.tests_directory, test)
            obj = os.path.join(self._cache_directory, f'{testname}.obj')
            pdb = os.path.join(self.build_directory, f'{testname}.pdb')
            exe = os.path.join(self.build_directory, f'{testname}.exe')

            print(f':BUILD> {self.name}::{testname}')
                
            # re-compile tests if modified
            force_link = False
            if _Should_Rebuild(uxx, obj):
                cmd = [ _Msvc_Tool.Compiler.value, _CFlag.Linkless.value ]
                cmd += self.config.compiler_args
                cmd += self.common_flags
                # cmd += self.header_units.included
                cmd += self.ifc_maps.compiler_args
                
                if os.path.exists(self.ifc_map):
                    cmd.append(_IfcFlag.IfcMap.value)
                    cmd.append(self.ifc_map)

                cmd.append(_IfcFlag.TranslationUnit.value)
                cmd.append(uxx)
                cmd.append(f'/Fo{obj}')
                cmd.append(f'/Fd{pdb}')
                cmd.append(f'/Fe{exe}')
                _Shell_Exec(*cmd)
                force_link = True

            # re-link tests if project output file was modified
            if force_link or _Should_Rebuild(self.output_file, exe):
                cmd = [ _Msvc_Tool.Linker.value ]
                cmd += self.config.linker_args
                cmd.append(obj)
                
                if self.type == ProjectType.LIB:
                    cmd.append(self.output_file)
                    
                cmd.append(f'/PDB:{pdb}')
                cmd.append(f'/OUT:{exe}')
                _Shell_Exec(*cmd)
                print(f':BUILT> {self.name}::{testname}')
            else:
                print(f':> Not building {self.name}::{testname} (no changes).')
        
        print('---')

    @property
    def ifc_map(self):
        return _Path_Join(self.build_directory, 'ifcMap.toml')

    def generate_ifc_map(self):
        toml = self.ifc_map
        if _Should_Rebuild(self.output_file, toml):
            with open(toml, 'w') as ifc_map:
                for name, ifc in self.header_units.exported.items():
                    ifc_map.write( _TOML_HEADER_UNIT_TEMPLATE % (name, ifc) )
                
                for name, ifc in self.modules.exported.items():
                    ifc_map.write( _TOML_MODULE_TEMPLATE % (name, ifc) )
            print(f':> Wrote IFC map to {toml}')
    
    def link_libraries(self, *libs: 'Project'):
        for lib in libs:
            assert lib.type == ProjectType.LIB, 'linking DLL is not implemented yet ((('
            assert not lib.name in self.ifc_maps.external, f'library {lib.name} is already linked' 
            # assert os.path.exists(lib.ifc_map), f'could not find ifc map for library {lib.name}'
            assert os.path.exists(lib.output_file), f'could not find {lib.output_file}'
            self.object_files.included.append(lib.output_file)
            if os.path.exists(lib.ifc_map):
                self.ifc_maps.external[lib.name] = lib.ifc_map

#
# Resembles MSBuild's terminology:
#   Solution is same to Project
#   as .sln to .proj files
#
@dataclass
class Solution:
    name: str
    source_directory: os.PathLike
    build_directory: os.PathLike
    output_directory: os.PathLike
    config: Config = field(default_factory=lambda: Config(ConfigType.Debug))
    projects: list[Project] = field(default_factory=list)

    def project(self, name: str, sources: list[os.PathLike]) -> Project:
        assert not name in self.projects

        proj = Project(name=name, config=self.config, type=ProjectType.LIB, 
            source_directory=_Path_Join(self.source_directory, name, 'modules'),
            tests_directory=_Path_Join(self.source_directory, name, 'tests'),
            build_directory=_Path_Join(self.build_directory, name),
        ).add_sources(sources)

        self.projects.append(proj)
        return proj

    def _copy_output(self):
        if not self.output_directory:
            return
        
        from shutil import copy2, rmtree
        rmtree(self.output_directory, ignore_errors=True)
        os.makedirs(self.output_directory, exist_ok=True)
        
        for directory_, _, files_ in os.walk(self.build_directory):
            for file_ in files_:
                match os.path.splitext(file_)[1]:
                    case '.exe' | '.lib' | '.dll' | '.pdb':
                        copy2(os.path.join(directory_, file_), self.output_directory)
                    case _:
                        pass

    def build(self, target: str):
        print('BUILDING TARGET', target)
        for proj in self.projects:
            proj.on_target(target=target)
        
        self._copy_output()

