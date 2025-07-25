#!/usr/bin/python3 -I

import argparse
import asteval
import os
import psutil
import random
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time

SELF = psutil.Process(os.getpid())
PARENT = SELF.parent()

#
# Output
#

_args = None
_last_type = None
_open_files = { 0: sys.stdin, 1: sys.stdout, 2: sys.stderr}
def print_msg(mtype, *msgs):
    #pylint:disable=global-statement
    global _last_type

    fd = getattr(_args, f"chio_{mtype}_fd", -1) #pyltint:disable=used-before-assignment
    if fd == -1:
        return
    if fd not in _open_files:
        _open_files[fd] = os.fdopen(fd, "w")
    f = _open_files[fd]
    if _last_type and _last_type != mtype:
        print("", file=f)
    _last_type = mtype
    print(f"[{mtype.upper()}]", *msgs, file=f, flush=True)

def print_info(*msgs):
    print_msg("info", *msgs)
def print_warn(*msgs):
    print_msg("warn", *msgs)
def print_hint(*msgs):
    print_msg("hint", *msgs)
def print_test(*msgs):
    print_msg("test", *msgs)
def print_pass(*msgs):
    print_msg("pass", *msgs)
def print_fail(*msgs):
    print_msg("fail", *msgs)
def print_flag(*msgs):
    print_msg("flag", *msgs)
def print_hype(*msgs):
    print_msg("hype", *msgs)

#
# Checking processes.
#

def check_exe_basename(process, basename, basename_regex=None):
    if basename_regex is None:
        basename_regex = basename
    print_info(f"The process' executable is {process.exe()}.")
    if os.path.basename(process.exe()) == "docker-init":
        print_warn("This process is the initialization process of your docker container (aka PID 1).")
        print_warn("When the parent of a process terminates, that process is 'reparented' to PID 1.")
        print_warn("So, the likely situation here is that your parent process terminated before")
        print_warn("waiting on the child. Go fix that :-). Look into waitpid() in C, process.wait() for")
        print_warn("pwntools, or Popen.wait() for subprocess.")
    else:
        print_info("This might be different than expected because of symbolic links (for example, from /usr/bin/python to /usr/bin/python3 to /usr/bin/python3.8).")

    goal_basename = os.path.basename(os.path.realpath(shutil.which(basename)))
    found_basename = os.path.basename(os.path.realpath(shutil.which(process.exe())))
    print_info(f"To pass the checks, the executable must be {goal_basename}.")
    assert re.match(basename_regex, os.path.basename(os.path.realpath(shutil.which(process.exe())))), f"Executable must be '{goal_basename}'. Yours is: {found_basename}"

def check_ipython(process):
    print_test("We will now check that that the process is an interactive ipython instance.")
    print_info("Since ipython runs as a script inside python, this will check a few things:")
    print_info("1. That the process itself is python.")
    print_info("2. That the module being run in python is ipython.")
    print_info("If the process being checked is just a normal 'ipython', you'll be okay!")
    check_exe_basename(process, 'python', r"python(\d(\.\d+)?)?$")
    assert re.match(r".*ipython.*", ' '.join(process.cmdline())), "It does not look like the module being run is ipython."
    assert len(process.cmdline()) == 2, "ipython must be running in its default, interactive mode (i.e., ipython with no commandline arguments)."

def check_python(process):
    print_test("We will now check that that the process is a non-interactive python instance (i.e., an executing python script).")
    check_exe_basename(process, 'python', r"python(\d(\.\d+)?)?$")
    assert len(process.cmdline()) == 2 and process.cmdline()[1].endswith(".py"), "The python process must be executing a python script that you wrote like this: `python my_script.py`"

def check_binary(process):
    print_test("Checking to make sure that the process is a custom binary that you created by compiling a C program")
    print_test("that you wrote. Make sure your C program has a function called 'pwncollege' in it --- otherwise,")
    print_test("it won't pass the checks.")

    if process == PARENT:
        print_hint("If this is a check for the *parent* process, keep in mind that the exec() family of system calls")
        print_hint("does NOT result in a parent-child relationship. The exec()ed process simply replaces the exec()ing")
        print_hint("process. Parent-child relationships are created when a process fork()s off a child-copy of itself,")
        print_hint("and the child-copy can then execve() a process that will be the new child. If we're checking for a")
        print_hint("parent process, that's how you make that relationship.")

    print_info(f"The executable that we are checking is: {process.exe()}.")
    if os.path.basename(process.exe()) in [ "bash", "dash", "docker-init" ]:
        print_hint("One frequent cause of the executable unexpectedly being a shell or docker-init is that your")
        print_hint("parent process terminated before this check was run. This happens when your parent process launches")
        print_hint("the child but does not wait on it! Look into the waitpid() system call to wait on the child!")
        print_hint("")
        print_hint("Another frequent cause is the use of system() or popen() to execute the challenge. Both will actually")
        print_hint("execute a shell that will then execute the challenge, so the parent of the challenge will be that")
        print_hint("shell, rather than your program. You must use fork() and one of the exec family of functions (execve(),")
        print_hint("execl(), etc).")

    assert process.exe().startswith("/home"), "The process must be your own program in your own home directory."
    assert len(process.cmdline()) == 1, "The process must have been called with no commandline arguments (argc == 1)."
    assert b"ELF 64" in subprocess.check_output(["/usr/bin/file", process.exe() ], stderr=subprocess.PIPE), "The program must be a compiled C program."
    assert b"pwncollege" in subprocess.check_output([ "/usr/bin/nm", "-a", process.exe() ], stderr=subprocess.PIPE), "The program must contain a function named 'pwncollege'."

def check_bash(process):
    print_test("Checking to make sure the process is the bash shell. If this is a check for the parent process, then,")
    print_test("most likely, this is what you do by default anyways, but we'll check just in case...")
    check_exe_basename(process, 'bash', r'.*bash$')
    assert len(process.cmdline()) == 1, f"The shell process must be running in its default, interactive mode (/bin/bash with no commandline arguments). Your commandline arguments are: {process.cmdline()}"

def check_shellscript(process):
    print_test("Checking to make sure the process is a non-interactive shell script.")

    assert os.path.basename(process.exe()) in [ 'sh', 'bash' ], f"Process interpreter must be 'sh' or 'bash'. Yours is: {os.path.basename(process.exe())}"
    assert len(process.cmdline()) == 2 and process.cmdline()[1].endswith(".sh"), "The shell process must be executing a shell script that you wrote like this: `bash my_script.sh`"

def check_challenge_shellscript(process):
    print_test("Checking to make sure the process is a non-interactive shell script running in /challenge.")

    assert os.path.basename(process.exe()) in [ 'sh', 'bash' ], f"Process interpreter must be 'sh' or 'bash'. Yours is: {os.path.basename(process.exe())}"
    assert len(process.cmdline()) > 1, "The shell must be running in non-interactive mode (with a script)!"
    if process.cmdline()[1] == "-c":
        assert process.cmdline()[3].startswith("/challenge"), f"The shell process must be executing a shell script under /challenge! Yours is: {process.cmdline()[3]}"
    else:
        assert process.cmdline()[1].startswith("/challenge"), f"The shell process must be executing a shell script under /challenge! Yours is: {process.cmdline()[1]}"

PROCESS_TYPE_CHECKERS = {
    'python': check_python,
    'bash': check_bash,
    'shellscript': check_shellscript,
    'challenge_shellscript': lambda p: check_challenge_shellscript(p),
    'ipython': check_ipython,
    'binary': check_binary,
    'netcat': lambda p: check_exe_basename(p, 'nc'),
    'socat': lambda p: check_exe_basename(p, 'socat'),
    'echo': lambda p: check_exe_basename(p, 'echo'),
    'cat': lambda p: check_exe_basename(p, 'cat'),
    'grep': lambda p: check_exe_basename(p, 'grep'),
    'sed': lambda p: check_exe_basename(p, 'sed'),
    'tee': lambda p: check_exe_basename(p, 'tee'),
    'find': lambda p: check_exe_basename(p, 'find'),
    'rev': lambda p: check_exe_basename(p, 'rev'),
    'diff': lambda p: check_exe_basename(p, 'diff'),
}

#
# Checking FD redirection
#

def resolve_fd_path(pid, fd):
    path = os.path.realpath(f"/proc/{pid}/fd/{fd}")
    if path.startswith(f"/proc/{pid}/fd/"):
        # fixup for sockets and pipes
        path = os.path.basename(path)
    return path

def name_fd(fd):
    return "stdin" if fd == 0 else "stdout" if fd == 1 else "stderr" if fd == 2 else f"file descriptor {fd}"

def check_fd_path(fd, path, verbose=False):
    if verbose:
        print_test(f"I will now check that you redirected {path} to/from my {name_fd(fd)}.")

    print_hint("File descriptors are inherited from the parent, unless the FD_CLOEXEC is set by the parent on the file descriptor.")
    print_hint("For security reasons, some programs, such as python, do this by default in certain cases. Be careful if you are")
    print_hint("creating and trying to pass in FDs in python.")

    actual_path = resolve_fd_path(os.getpid(), fd)
    assert os.path.exists(actual_path) and not actual_path.startswith("/dev/pts"), f"You have not redirected anything for this process' {name_fd(fd)}."
    assert actual_path == os.path.realpath(path), f"You have redirected the wrong file for {name_fd(fd)} ({actual_path} instead of {path})."

def check_stdin_path(path):
    check_fd_path(0, path)
def check_stdout_path(path):
    check_fd_path(1, path)
def check_stderr_path(path):
    check_fd_path(2, path)

def check_fifo(fd):
    print_hint("A FIFO stands for First In First Out, a type of special file that passes data between processes that write to it and")
    print_hint("processes that read from it. Look at the mkfifo man page and play around with FIFOs on the commandline to get a feel")
    print_hint("for them.")

    path = resolve_fd_path(os.getpid(), fd)
    assert os.path.exists(path) and not path.startswith("/dev/pts"), f"You have not redirected anything to/from this process' {name_fd(fd)}."
    assert stat.S_ISFIFO(os.stat(path).st_mode), f"{name_fd(fd)} is not referencing a FIFO."

#
# Checking FD partners.
#

def resolve_fd_socket_partner(pid, fd):
    our_socket = resolve_fd_path(pid, fd)
    assert our_socket.startswith("socket:"), "You did not make a network connection to this process."

    # this is dumb, but works
    for p in (SELF, SELF.parent(), SELF.parent().parent()):
        try:
            our_connection = next(c for c in p.connections() if resolve_fd_path(p.pid, c.fd) == our_socket)
            break
        except StopIteration:
            pass
    else:
        raise RuntimeError("Connection check failed to find a connection. Please report this; it is not your fault.")

    try:
        their_pid = next(o for o in psutil.net_connections() if o.raddr == our_connection.laddr).pid
    except StopIteration:
        #pylint: disable=raise-missing-from
        raise AssertionError("You did not make a connection from within this container, or your client process terminated prematurely.")
    return their_pid

def resolve_fd_pipe_partner(pid, fd, parent_ok=False):
    our_pipe = resolve_fd_path(pid, fd)
    assert our_pipe.startswith("pipe:"), f"{name_fd(fd)} of this process does not appear to be a pipe!"

    for p in psutil.process_iter():
        if p == SELF:
            continue
        if p.pid == PARENT.pid and not parent_ok:
            continue

        try:
            for ofd in os.listdir(f"/proc/{p.pid}/fd"):
                their_pipe = resolve_fd_path(p.pid, int(ofd))
                if their_pipe == our_pipe:
                    return p.pid
        except PermissionError:
            pass

    raise AssertionError(f"Unable to find the process on the other end of the {name_fd(fd)} pipe. There are many possible reasons for this, with the following three being the most likely:\n\t(1) The process on the other end of the pipe was launched with invalid arguments and quickly errored out, so it was gone by the time we checked. If that's the case, figure out the right arguments!\n\t(2) The process on the other end of the pipe was a fast-running processs (such as `cat some_file`, which just yeets the file to its stdout and exits). If that's the case, figure out how to make the process stick around!\n\t(3) This check happened *before* the other process successfully launched. This is a common occurrence if you're trying to redirect the stdout of this challenge to another process, you're manually doing this in an interactive ipython, and you're launching the challenge before launching the other process. Try pasting in both pwntools or subprocess invocations rapidly one after the other to get the second process launched in time!")

#
# Other checks
#

def check_env_count(n):
    e = dict(os.environ)
    # we can't get rid of LC_CTYPE in python, and PWD when launching from pwntools, so let's take it easy on them
    if "LC_CTYPE" in e:
        e.pop("LC_CTYPE")
    if "PWD" in e:
        e.pop("PWD")
    actual_num = len(e.keys())
    assert actual_num == n, f"You should launch this program with {n} environment variables, but you have {actual_num}!"

def check_cwd(process, cwd):
    p_cwd = process.cwd()
    assert p_cwd == cwd, f"My current working directory is incorrect! It should be '{cwd}', but it is '{p_cwd}'."

def check_arg(args, n, v):
    if n == 0:
        print_hint("argv[0] is passed into the execve() system call *separately* from the program path to execute.")
        print_hint("This means that it does not have to be the same as the program path, and that you can actually")
        print_hint("control it. This is done differently for different methods of execution. For example, in C, you")
        print_hint("simply need to pass in a different argv[0]. Bash has several ways to do it, but one way is to")
        print_hint("use a combination of a symbolic link (e.g., the `ln -s` command) and the PATH environment variable.")

    assert len(args) >= n, "It looks like you did not pass enough arguments to the program."
    assert args[n] == v, f"argv[{n}] is not '{v}' (it seems to be '{args[n]}', instead)."

def check_env(k, v):
    assert k in os.environ, f"Your environment does not have a variable {k}."
    assert os.environ[k] == v, f"The value of environment variable {k} is not '{v}' (it seems to be '{os.environ[k]}', instead)."

#
# Challenge-response
#

def generate_challenge(ops, depth, myrand):
    if depth == 0:
        return str(myrand.randrange(1, 0x1000))

    left = generate_challenge(ops, depth-1, myrand)
    right = generate_challenge(ops, depth-1, myrand)
    op = myrand.choice(ops)

    # make sure we're not dividing by zero
    challenge = f"({left}) {op} ({right})" if depth > 1 else f"{left}{op}{right}"
    if asteval.Interpreter()(challenge) is None:
        return generate_challenge(ops, depth, myrand)
    else:
        return challenge

def check_challenges(num, ops, depth, myrand=random):
    for _ in range(num):
        challenge = generate_challenge(ops, myrand.randrange(depth), myrand)
        print_test(f"CHALLENGE! Please send the solution for: {challenge}")
        response = input()
        expected = str(asteval.Interpreter()(challenge))
        assert response == expected, f"Your response is incorrect! I expected {expected} but got {response}."
        print_pass("CORRECT!")

def check_password(password):
    print_info("Reading in your input now...")
    response = input().strip()
    assert response == password, f"You entered the wrong password ({response} instead of {password})."

#
# Signal handling
#

SIGNALS = [ "SIGUSR1", "SIGUSR2", "SIGINT", "SIGABRT", "SIGHUP" ]
EXPECTED_SIGNALS = [ ]

def handle_signal(snum, _):
    print_info(f"Received signal {snum}! Is it correct?")
    if snum == getattr(signal, EXPECTED_SIGNALS[-1]):
        print_pass("Correct!")
        EXPECTED_SIGNALS.pop()
    else:
        print_fail("Incorrect signal received. Exiting.")
        sys.exit(1)

def setup_handlers():
    for s in SIGNALS:
        snum = getattr(signal, s)
        signal.signal(snum, handle_signal)

def check_signals(num, myrand=random):
    setup_handlers()
    EXPECTED_SIGNALS[:] = [ myrand.choice(SIGNALS) for _ in range(num) ]
    print_test(f"You must send me (PID {os.getpid()}) the following signals, in exactly this order: {EXPECTED_SIGNALS[::-1]}")
    while EXPECTED_SIGNALS:
        old_size = len(EXPECTED_SIGNALS)
        time.sleep(1)
        if len(EXPECTED_SIGNALS) != old_size:
            print_info("Nice, you sent one of the signals!")

#
# Main code
#

def do_checks(args):
    print_info("This challenge will perform a bunch of checks.")
    if args.reward:
        print_info(f"If you pass these checks, you will receive the {args.reward} file.")
    else:
        print_info("Good luck!")

    if args.parent:
        print_test("Performing checks on the parent process of this process.")
        PROCESS_TYPE_CHECKERS[args.parent](PARENT)
        print_pass("You have passed the checks on the parent process!")

    if args.client:
        print_test("This is a network server. Trying to determine the client process...")
        client = psutil.Process(resolve_fd_socket_partner(os.getpid(), 0))
        print_test("Performing tests on the client process!")
        PROCESS_TYPE_CHECKERS[args.client](client)
        print_pass("You have passed the checks on the client process!")

    if args.check_stdin_pipe:
        print_test("You should have redirected another process to my stdin. Checking...")
        partner = psutil.Process(resolve_fd_pipe_partner(os.getpid(), 0, parent_ok=False))
        print_test("Performing checks on that process!")
        PROCESS_TYPE_CHECKERS[args.check_stdin_pipe](partner)
        print_pass("You have passed the checks on the process on the other end of my stdin!")
    if args.check_stdout_pipe:
        print_test("You should have redirected my stdout to another process. Checking...")
        time.sleep(1) # sleep to give the parent process enough time to spawn the partner, in case of stdout piping
        partner = psutil.Process(resolve_fd_pipe_partner(os.getpid(), 1, parent_ok=False))
        print_test("Performing checks on that process!")
        PROCESS_TYPE_CHECKERS[args.check_stdout_pipe](partner)
        print_pass("You have passed the checks on the process on the other end of my stdout!")
    if args.check_stderr_pipe:
        print_test("You should have redirected my stderr to another process. Checking...")
        time.sleep(1) # sleep to give the parent process enough time to spawn the partner, in case of stderr piping
        partner = psutil.Process(resolve_fd_pipe_partner(os.getpid(), 2, parent_ok=False))
        print_test("Performing checks on that process!")
        PROCESS_TYPE_CHECKERS[args.check_stderr_pipe](partner)
        print_pass("You have passed the checks on the process on the other end of my stderr!")

    if args.check_stdin_parent:
        print_test("You should have connected my stdin to my parent process. Checking...")
        partner = psutil.Process(resolve_fd_pipe_partner(os.getpid(), 0, parent_ok=True))
        assert partner == PARENT, "It looks like stdin is connected to some other process than my parent!"
        print_pass("Looks like you connected my stdin to my parent process!")
    if args.check_stdout_parent:
        print_test("You should have connected my stdout to my parent process. Checking...")
        partner = psutil.Process(resolve_fd_pipe_partner(os.getpid(), 1, parent_ok=True))
        assert partner == PARENT, "It looks like stdout is connected to some other process than my parent!"
        print_pass("Looks like you connected my stdout to my parent process!")

    if args.check_stdin_path:
        print_test(f"You should have redirected a file called {args.check_stdin_path} to my stdin. Checking...")
        check_stdin_path(args.check_stdin_path)
        print_pass("The file at the other end of my stdin looks okay!")
    if args.check_stdout_path:
        print_test(f"You should have redirected my stdout to a file called {args.check_stdout_path}. Checking...")
        check_stdout_path(args.check_stdout_path)
        print_pass("The file at the other end of my stdout looks okay!")
    if args.check_stderr_path:
        print_test(f"You should have redirected my stderr to {args.check_stderr_path}. Checking...")
        check_stderr_path(args.check_stderr_path)
        print_pass("The file at the other end of my stderr looks okay!")

    if args.check_stdin_fifo:
        print_test("You should have redirected a FIFO to my stdin. Checking...")
        check_fifo(0)
        print_pass("Looks like my stdin is connected to a FIFO!")
    if args.check_stdout_fifo:
        print_test("You should have redirected my stdout to a FIFO. Checking...")
        check_fifo(1)
        print_pass("Looks like my stdout is connected to a FIFO!")

    if args.cwd:
        print_test(f"You should launch me with a working directory of {args.cwd}.")
        check_cwd(SELF, args.cwd)
        print_pass("Looks like my working directory is correct!")

    if args.parent_different_cwd:
        print_test("My working directory should be different than the parent process'!")
        print_info(f"My working directory is: {SELF.cwd()}.")
        print_info(f"Parent working directory is: {PARENT.cwd()}.")
        assert SELF.cwd() != PARENT.cwd(), "Parent process' and this process' working directories are the same!"
        print_pass("Looks like my working directory is different than my parent's!")

    if args.check_arg:
        ns,v = args.check_arg.split(":")
        n = int(ns)
        print_test(f"My argv[{n}] should have a value of {v}! Let's check...")
        check_arg(args.old_args[1:], n, v)
        print_pass("You successfully passed the argument value check!")

    if args.check_env:
        k,v = args.check_env.split(":")
        print_test(f"My '{k}' environment variable should have a value of {v}! Let's check...")
        check_env(k, v)
        print_pass("You successfully passed the environment value check!")

    if args.empty_env:
        print_test(f"You should launch me with an {'otherwise-' if args.check_env else ''}empty environment. Checking...")
        check_env_count(1 if args.check_env else 0)
        print_pass("You successfully passed the empty environment check!")

    if args.empty_argv:
        print_test("You should launch me with an empty argv (i.e., argc == 0). Checking...")
        assert not args.old_args[1:], f"argv is not empty, but has {len(args.old_args[1:])} entries..."
        print_pass("You successfully passed the empty argument check!")

    if args.password:
        print_test(f"This program expects you to enter a simple password (specifically, {args.password}). Send it now!")
        check_password(args.password)
        print_pass("You successfully passed the password!")

    if args.num_challenges:
        print_info(f"This program will send you {args.num_challenges} mathematical challenge{'s' if args.num_challenges>1 else ''} that you will need to compute responses for.")
        check_challenges(args.num_challenges, args.challenge_ops, args.challenge_depth)
        print_pass("You successfully passed the mathematical challenges!")

    if args.num_signals:
        print_info("This program will stop and wait for you to send it a number of signals. For more information on signals,")
        print_info("look at the man page of the kill command.")
        check_signals(args.num_signals)
        print_pass("You successfully passed the signal challenges!")
#
# Other stuff
#

def listen_dup(port):
    print_info(f"This challenge is a network server, and will only communicate on TCP port {port}.")
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(('localhost', port))
    s.listen()
    c,_ = s.accept()
    print_info("Connection received! All further communication will happen through the TCP connection.")
    os.dup2(c.fileno(), 0)
    os.dup2(c.fileno(), 1)
    os.dup2(c.fileno(), 2)

def input_dup(fd):
    print_test(f"This challenge takes input over {name_fd(fd)}! Make sure to provide this file descriptor to the program, and send any required input over it.")
    assert os.path.exists(f"/proc/{os.getpid()}/fd/{fd}"), f"It looks like there is no {name_fd(fd)} passed in to this process."
    os.dup2(fd, 0)
    print_pass("Preliminary checks are okay on the input FD!")

def setup_input(args):
    if args.listen_dup:
        listen_dup(args.listen_dup)

    if args.input_dup:
        input_dup(args.input_dup)

ARG_HELP = { }
def add_argument(parser, arg, **kwargs):
    if arg.startswith("--"):
        ARG_HELP[arg[2:]] = kwargs['help']
    return parser.add_argument(arg, **kwargs)

if __name__ == '__main__':
    _parser = argparse.ArgumentParser()

    # process checks
    add_argument(_parser, "--parent", choices=list(PROCESS_TYPE_CHECKERS.keys()), nargs='?', help="the challenge checks for a specific parent process")
    add_argument(_parser, "--client", choices=list(PROCESS_TYPE_CHECKERS.keys()), nargs='?', help="the challenge checks for a specific (network) client process")
    add_argument(_parser, "--check_stdin_pipe", choices=list(PROCESS_TYPE_CHECKERS.keys()), nargs='?', help="the challenge checks for a specific process at the other end of stdin")
    add_argument(_parser, "--check_stdout_pipe", choices=list(PROCESS_TYPE_CHECKERS.keys()), nargs='?', help="the challenge checks for a specific process at the other end of stdout")
    add_argument(_parser, "--check_stderr_pipe", choices=list(PROCESS_TYPE_CHECKERS.keys()), nargs='?', help="the challenge checks for a specific process at the other end of stderr")
    add_argument(_parser, "--check_stdin_parent", action='store_true', help="the challenge makes sure the parent is communicating with us over stdin")
    add_argument(_parser, "--check_stdout_parent", action='store_true', help="the challenge makes sure the parent is communicating with us over stdout")

    # i/o
    add_argument(_parser, "--listen_dup", type=int, nargs='?', help="the challenge will listen for input on a TCP port")
    add_argument(_parser, "--input_dup", type=int, nargs='?', help="the challenge will take input on a specific file descriptor")
    add_argument(_parser, "--check_stdin_path", type=str, nargs='?', help="the challenge will check that input is redirected from a specific file path")
    add_argument(_parser, "--check_stdout_path", type=str, nargs='?', help="the challenge will check that output is redirected to a specific file path")
    add_argument(_parser, "--check_stderr_path", type=str, nargs='?', help="the challenge will check that error output is redirected to a specific file path")
    add_argument(_parser, "--check_stdin_fifo", action='store_true', help="the challenge will make sure that stdin is redirected from a fifo")
    add_argument(_parser, "--check_stdout_fifo", action='store_true', help="the challenge will make sure that stdout is redirected to a fifo")

    # other process stuff
    add_argument(_parser, "--cwd", type=str, nargs='?', help="the challenge will check that it is running in a specific current working directory")
    add_argument(_parser, "--parent_different_cwd", action='store_true', help="the challenge will check to make sure that the parent's parent CWD to be different than the challenge's CWD")
    add_argument(_parser, "--empty_env", action='store_true', help="the challenge will check that the environment is empty (except LC_CTYPE, which is impossible to get rid of in some cases)")
    add_argument(_parser, "--empty_argv", action='store_true', help="the challenge will check that argv is empty (e.g., argc == 0)")

    # arg stuff
    add_argument(_parser, "--check_arg", type=str, nargs='?', help="the challenge will check that argv[NUM] holds value VALUE (listed to the right as NUM:VALUE)")
    add_argument(_parser, "--check_env", type=str, nargs='?', help="the challenge will check that env[KEY] holds value VALUE (listed to the right as KEY:VALUE)")

    # challenges
    add_argument(_parser, "--num_challenges", type=int, nargs='?', help="the challenge will force the parent process to solve a number of arithmetic problems")
    add_argument(_parser, "--challenge_ops", type=str, default="+", nargs='?', help="the challenge will use the following arithmetic operations in its arithmetic problems")
    add_argument(_parser, "--challenge_depth", type=int, default=1, nargs='?', help="the complexity (in terms of nested expressions) of the arithmetic problems")
    add_argument(_parser, "--password", type=str, nargs='?', help="the challenge will check for a hardcoded password over stdin")
    add_argument(_parser, "--num_signals", type=int, nargs='?', help="the challenge will require the parent to send number of signals")
    add_argument(_parser, "--reward", type=str, nargs='?', help="the challenge will output a reward file if all the tests pass")

    # chio behaviors
    #pylint:disable=consider-using-with
    add_argument(_parser, "--chio_info_fd", type=int, default=2, help="file to write info to (-1 to disable)")
    add_argument(_parser, "--chio_warn_fd", type=int, default=2, help="file to write warnings to (-1 to disable)")
    add_argument(_parser, "--chio_hint_fd", type=int, default=2, help="file to write hints to (-1 to disable)")
    add_argument(_parser, "--chio_test_fd", type=int, default=2, help="file to write things we're about to test to (-1 to disable)")
    add_argument(_parser, "--chio_pass_fd", type=int, default=2, help="file to write pass messages to (-1 to disable)")
    add_argument(_parser, "--chio_fail_fd", type=int, default=2, help="file to write fail messages to (-1 to disable)")
    add_argument(_parser, "--chio_flag_fd", type=int, default=2, help="file to write the flag to (-1 to disable)")
    add_argument(_parser, "--chio_hype_fd", type=int, default=2, help="file to write hype to (-1 to disable)")


    # remaining arguments
    _parser.add_argument("old_args", nargs=argparse.REMAINDER)

    _args = _parser.parse_args()

    assert (not _args.old_args) or _args.old_args[0] == "--", "ERROR: INVALID OLD_ARGV. Contact the profs."

    print_info("WELCOME! This challenge makes the following asks of you:")
    for _a,_v in vars(_args).items():
        if _a == 'old_args':
            continue
        if _a.startswith("chio"):
            continue
        if _v in ( None, False ):
            continue
        if _a in [ "challenge_ops", "challenge_depth" ] and not _args.num_challenges:
            continue
        if _v is True:
            print_info("-", ARG_HELP[_a])
        else:
            print_info("-", ARG_HELP[_a],":",_v)

    print_hype("ONWARDS TO GREATNESS!")

    try:
        setup_input(_args)
    except AssertionError as _e:
        print_fail("You did not satisfy all the execution requirements.")
        print_fail("Specifically, you must fix the following issue:")
        print_fail(f"  {_e}")
        sys.exit(1)

    try:
        do_checks(_args)
    except AssertionError as _e:
        print_fail("You did not satisfy all the execution requirements.")
        print_fail("Specifically, you must fix the following issue:")
        print_fail(f"  {_e}")
        sys.exit(2)

    print_pass("Success! You have satisfied all execution requirements.")
    if _args.reward:
        print_flag("Here is your flag:")
        print_flag(open(_args.reward).read()) #pylint:disable=unspecified-encoding,consider-using-with
    sys.exit(0)
