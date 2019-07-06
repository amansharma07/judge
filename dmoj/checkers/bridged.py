import os
import traceback
import tempfile

from dmoj.error import CompileError, InternalError
from dmoj.executors import executors
from dmoj.executors.base_executor import CompiledExecutor
from dmoj.judgeenv import env, get_problem_root
from dmoj.result import CheckerResult
from dmoj.utils import ansi
from dmoj.utils.unicode import utf8text

checker_defaults = {
    'time_limit': env['generator_time_limit'],
    'memory_limit': env['generator_memory_limit'],
    'compiler_time_limit': env['compiler_time_limit'],
    'feedback': True
}

executor = None

def get_executor(checker_kwargs, problem_id):
    global executor

    if executor is None:
        if 'files' not in checker_kwargs:
            raise InternalError('No checker file[s] specified!')
        if 'lang' not in checker_kwargs:
            raise InternalError('Language not specified for checker!')

        filenames = list(map(lambda x: os.path.join(get_problem_root(problem_id), x), checker_kwargs['files']))
        for filename in filenames:
            print(os.path.abspath(filename))
        sources = {}

        try:
            for filename in filenames:
                with open(filename, 'r') as f:
                    sources[os.path.basename(filename)] = f.read()
        except:
            traceback.print_exc()
            raise IOError('Could not read checker source!')

        use_c_or_cpp = any(map(lambda name: os.path.splitext(name)[1] in ['.c', '.cpp'], filenames))

        clazz = executors.get(checker_kwargs['lang'])
        if clazz is None:
            raise InternalError('Could not find an executor for language "%s"' % checker_kwargs['lang'])

        clazz = clazz.Executor

        fs = clazz.fs + [tempfile.gettempdir()]
        clazz = type('Executor', (clazz,), {'fs': fs})

        if issubclass(clazz, CompiledExecutor):
            compiler_time_limit = checker_kwargs['compiler_time_limit'] or clazz.compiler_time_limit
            clazz = type('Executor', (clazz,), {'compiler_time_limit': compiler_time_limit})

        if hasattr(clazz, 'flags'):
            flags = checker_kwargs.get('flags', [])

            flags += ['-DWINDOWS_JUDGE', '-DWIN32'] if os.name == 'nt' else ['-DLINUX_JUDGE']

            # We shouldn't be mutating the base class flags.
            # See <https://github.com/DMOJ/judge/issues/174>.
            clazz = type('FlaggedExecutor', (clazz,), {'flags': flags + list(clazz.flags)})

        try:
            # Optimize the common case.
            if use_c_or_cpp:
                # Some checkers (like those using testlib.h) take an extremely long time
                # to compile, so we cache them.
                executor = clazz('_checker', None, aux_sources=sources, cached=True)
            else:
                if len(sources) > 1:
                    raise InternalError('non-C/C++ checker cannot be multi-file')
                executor = clazz('_checker', list(sources.values())[0])
        except CompileError as err:
            # Strip ANSI codes from CompileError message so we don't get wacky displays on the site like
            # 01m[K_checker.cpp:26:23:[m[K [01;31m[Kerror: [m[K'[01m[Kgets[m[K' was not declared in this scope
            raise CompileError(ansi.strip_ansi(err.args[0]))

    return executor

def mktemp(data):
    tmp = tempfile.NamedTemporaryFile()
    tmp.write(data)
    tmp.flush()
    return tmp

def check(process_output, judge_output, judge_input, checker_kwargs, problem_id, point_value = None, **kwargs):
    checker_kwargs.update(checker_defaults)
    executor = get_executor(checker_kwargs, problem_id)

    with mktemp(judge_input) as arg1, mktemp(process_output) as arg2, mktemp(judge_output) as arg3:
        process = executor.launch(arg1.name, arg2.name, arg3.name, memory=checker_kwargs['memory_limit'],
                                  time=checker_kwargs['time_limit'])

        proc_output, error = map(utf8text, process.communicate())

        # We use the testlib.h return codes
        # 0 - AC
        # 1 - WA
        # 2 - WA (Presentation Error)
        # 3 - Internal Error

        if process.returncode == 0:
            if checker_kwargs['feedback']:
                return CheckerResult(True, point_value, feedback=proc_output)
            else:
                return True
        elif process.returncode in [1, 2]:
            if checker_kwargs['feedback']:
                return CheckerResult(False, 0, feedback=proc_output)
            else:
                return CheckerResult(False, 0, feedback='Presentation Error' if process.returncode == 2 else '')
        else:
            if process.returncode == 3:
                error = 'Checker failed assertion with message %s' % proc_output
            else:
                error = 'Checker returned unexpected return code %d with stderr %s' % (process.returncode, error)
            raise InternalError(error)