"""Known library calls in Rust, C, C++ and C# that touch files or run commands.

A call is only recorded when both are true:
  1. it is proven to be the library function: the full name is written (std::fs::write, System.IO.File.Exists),
     or a `use` / `using` / `namespace x =` line in the same file gives it, or (bare C names like fopen)
     the header is included by the file or by a project header it includes;
  2. the path or command is a plain string written in the code.
A known call with any other argument is listed as unknown with the reason. Nothing else is guessed.

Effect specs:
  [(kind, arg index), ...]  path at that argument; kind is reads/writes/lists/creates/deletes/moves/changes/checks
  "fopen"   path arg 0, mode string arg 1      "open"  path arg 0, O_* flags arg 1
  "shell"   one command line string (system, popen)
  "exec"    program path arg 0 (exec family, Rust Command::new, C# Process.Start)
"""

RUST = {
    "std::fs::read_to_string": [("reads", 0)], "std::fs::read": [("reads", 0)],
    "std::fs::read_link": [("reads", 0)], "std::fs::File::open": [("reads", 0)],
    "std::fs::write": [("writes", 0)], "std::fs::File::create": [("writes", 0)],
    "std::fs::remove_file": [("deletes", 0)], "std::fs::remove_dir": [("deletes", 0)],
    "std::fs::remove_dir_all": [("deletes", 0)],
    "std::fs::create_dir": [("creates", 0)], "std::fs::create_dir_all": [("creates", 0)],
    "std::fs::read_dir": [("lists", 0)], "std::fs::rename": [("moves", 0)],
    "std::fs::copy": [("reads", 0), ("writes", 1)], "std::fs::metadata": [("checks", 0)],
    "std::fs::set_permissions": [("changes", 0)],
    "std::process::Command::new": "exec",
}

# Bare C names (also C++): spec and the headers that declare them.
C = {
    "fopen": ("fopen", {"stdio.h", "cstdio"}),
    "open": ("open", {"fcntl.h"}),
    "remove": ([("deletes", 0)], {"stdio.h", "cstdio"}),
    "unlink": ([("deletes", 0)], {"unistd.h"}),
    "rmdir": ([("deletes", 0)], {"unistd.h"}),
    "rename": ([("moves", 0)], {"stdio.h", "cstdio"}),
    "mkdir": ([("creates", 0)], {"sys/stat.h"}),
    "chmod": ([("changes", 0)], {"sys/stat.h"}),
    "opendir": ([("lists", 0)], {"dirent.h"}),
    "access": ([("checks", 0)], {"unistd.h"}),
    "system": ("shell", {"stdlib.h", "cstdlib"}),
    "popen": ("shell", {"stdio.h"}),
    "execl": ("exec", {"unistd.h"}), "execlp": ("exec", {"unistd.h"}), "execle": ("exec", {"unistd.h"}),
    "execv": ("exec", {"unistd.h"}), "execvp": ("exec", {"unistd.h"}), "execvpe": ("exec", {"unistd.h"}),
}

CPP = {
    "std::filesystem::remove": [("deletes", 0)], "std::filesystem::remove_all": [("deletes", 0)],
    "std::filesystem::create_directory": [("creates", 0)], "std::filesystem::create_directories": [("creates", 0)],
    "std::filesystem::exists": [("checks", 0)], "std::filesystem::rename": [("moves", 0)],
    "std::filesystem::copy": [("reads", 0), ("writes", 1)], "std::filesystem::copy_file": [("reads", 0), ("writes", 1)],
    "std::filesystem::permissions": [("changes", 0)],
}
CPP_STREAMS = {"std::ifstream": "reads", "std::wifstream": "reads", "std::ofstream": "writes", "std::wofstream": "writes"}

CS = {"System.Diagnostics.Process.Start": "exec",
      "System.IO.File.Delete": [("deletes", 0)], "System.IO.File.Exists": [("checks", 0)],
      "System.IO.File.Move": [("moves", 0)], "System.IO.File.Copy": [("reads", 0), ("writes", 1)],
      "System.IO.Directory.CreateDirectory": [("creates", 0)], "System.IO.Directory.Delete": [("deletes", 0)],
      "System.IO.Directory.Exists": [("checks", 0)], "System.IO.Directory.Move": [("moves", 0)]}
for _n in ("ReadAllText", "ReadAllLines", "ReadAllBytes", "ReadLines", "OpenRead", "OpenText"):
    CS["System.IO.File." + _n] = [("reads", 0)]
for _n in ("WriteAllText", "WriteAllLines", "WriteAllBytes", "AppendAllText", "AppendAllLines", "Create",
           "CreateText", "OpenWrite", "AppendText"):
    CS["System.IO.File." + _n] = [("writes", 0)]
for _n in ("GetFiles", "GetDirectories", "EnumerateFiles", "EnumerateDirectories"):
    CS["System.IO.Directory." + _n] = [("lists", 0)]
CS_NEW = {"System.IO.StreamReader": "reads", "System.IO.StreamWriter": "writes"}

# A shell line with any of these does more than run one program with plain words.
SHELL_SPECIAL = set("|;&<>$`()\n*?~\"'\\{}[]#=")
WRITE_FLAGS = ("O_WRONLY", "O_RDWR", "O_CREAT", "O_TRUNC", "O_APPEND")
