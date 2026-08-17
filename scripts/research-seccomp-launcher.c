#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <linux/filter.h>
#include <linux/seccomp.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <unistd.h>

#define MAX_FILTER_INSTRUCTIONS 4096U

static void fail(const char *message) {
    perror(message);
    exit(1);
}

static void read_exact(int descriptor, unsigned char *buffer, size_t size) {
    size_t offset = 0;
    while (offset < size) {
        ssize_t observed = read(descriptor, buffer + offset, size - offset);
        if (observed < 0) {
            if (errno == EINTR) {
                continue;
            }
            fail("read seccomp filter");
        }
        if (observed == 0) {
            fputs("short seccomp filter read\n", stderr);
            exit(1);
        }
        offset += (size_t)observed;
    }
}

int main(int argc, char **argv) {
    if (argc < 3) {
        fputs("usage: research-seccomp-launcher <filter> <program> [args...]\n", stderr);
        return 2;
    }

    int descriptor = open(argv[1], O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (descriptor < 0) {
        fail("open seccomp filter");
    }

    struct stat observed;
    if (fstat(descriptor, &observed) != 0) {
        fail("stat seccomp filter");
    }
    if (!S_ISREG(observed.st_mode)
        || observed.st_size <= 0
        || (observed.st_size % (off_t)sizeof(struct sock_filter)) != 0) {
        fputs("invalid seccomp filter file\n", stderr);
        return 1;
    }

    size_t instruction_count = (size_t)observed.st_size / sizeof(struct sock_filter);
    if (instruction_count > MAX_FILTER_INSTRUCTIONS || instruction_count > USHRT_MAX) {
        fputs("seccomp filter exceeds instruction bound\n", stderr);
        return 1;
    }

    struct sock_filter *instructions = malloc((size_t)observed.st_size);
    if (instructions == NULL) {
        fail("allocate seccomp filter");
    }
    read_exact(descriptor, (unsigned char *)instructions, (size_t)observed.st_size);
    if (close(descriptor) != 0) {
        fail("close seccomp filter");
    }

    struct sock_fprog program = {
        .len = (unsigned short)instruction_count,
        .filter = instructions,
    };
    if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0) {
        fail("set no_new_privs");
    }
    if (prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, &program) != 0) {
        fail("install seccomp filter");
    }

    free(instructions);
    execvp(argv[2], &argv[2]);
    fail("execute isolated program");
    return 1;
}
