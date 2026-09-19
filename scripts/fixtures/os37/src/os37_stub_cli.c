/* OS-37 R-A leg 4 fixture: a NATIVE stub CLI.
 *
 * Why this exists in C rather than as one more shell script.  DESIGN §D5.3(4) R-A requires
 * the pty's foreground process to have the profile's binary as its EXECUTABLE IMAGE.  A
 * script can never satisfy that on any POSIX system -- the kernel loads its interpreter, so
 * the image of `#!/bin/sh ...` is `/bin/sh` no matter what the file is called.  Both real
 * MVP CLIs ship as native executables, so a native fixture is what actually resembles them;
 * a shell-script fixture would have forced the readiness proof to be weakened to fit the
 * test, which is the inversion this file exists to avoid.
 *
 * It reimplements nothing.  The behaviour still lives in `bin/os37-stub-cli`, the reviewed
 * shell fixture, which this binary runs as a CHILD with stdio inherited -- exactly the shape
 * of a real CLI that shells out to a helper.  The foreground process group's LEADER, whose
 * image R-A reads, is this binary; the helper is a subprocess of it and is never mistaken
 * for it.  The script's absolute path is baked in at compile time via -DSTUB_SCRIPT so the
 * fixture cannot silently pick up a different one from the environment.
 */
#include <stdlib.h>
#include <unistd.h>
#include <sys/wait.h>

#ifndef STUB_SCRIPT
#define STUB_SCRIPT "/nonexistent/os37-stub-cli"
#endif

int main(int argc, char **argv) {
    char **child_argv = (char **)calloc((size_t)argc + 2, sizeof(char *));
    if (child_argv == NULL) {
        return 127;
    }
    /* argv[0] is the SCRIPT's own path: the script uses `dirname "$0"` to find its
     * sibling fixture data, and it must keep resolving to the fixture directory. */
    child_argv[0] = (char *)STUB_SCRIPT;
    for (int i = 1; i < argc; i++) {
        child_argv[i] = argv[i];
    }
    child_argv[argc] = NULL;

    pid_t child = fork();
    if (child < 0) {
        return 127;
    }
    if (child == 0) {
        execv(STUB_SCRIPT, child_argv);
        _exit(127);
    }
    int status = 0;
    if (waitpid(child, &status, 0) < 0) {
        return 127;
    }
    if (WIFEXITED(status)) {
        return WEXITSTATUS(status);
    }
    if (WIFSIGNALED(status)) {
        return 128 + WTERMSIG(status);
    }
    return 1;
}
