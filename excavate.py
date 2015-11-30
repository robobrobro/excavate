#!/usr/bin/env python

""" Collects build artifacts from a git working directory. """

__prog__ = 'excavate'
__description__ = 'Excavates build artifacts from a projet and stores them in compressed tarballs.'
__version__ = '0.1.2'

import contextlib
import glob
import itertools
import os
import re
import select
import subprocess
import sys
import tarfile

class Logger(object):
    """ Handles logging debugging messages """

    def __init__(self, verbosity, *args, **kwargs):
        """ Creates a Logger object """
        self.__verbosity = verbosity

    def __log(self, msg, logfile, verbosity=1, color=None, *args, **kwargs):
        if self.__verbosity >= verbosity:
            if color is not None and logfile.isatty():
                msg = '\033[{color}m{msg}\033[0m'.format(color=color, msg=msg)
            logfile.write(msg)

    def log(self, msg, *args, **kwargs):
        """ Logs a message to stdout """
        self.__log(msg, sys.stdout, *args, **kwargs)

    def debug(self, msg, *args, **kwargs):
        """ Logs an error message to stdout if Logger's verbosity is >= verbosity """
        self.__log(msg, sys.stdout, verbosity=2, *args, **kwargs)

    def err(self, msg, *args, **kwargs):
        """ Logs an error message to stderr """
        self.__log(msg, sys.stderr, *args, **kwargs)

def _check_output(logger, command, *args, **kwargs):
    if hasattr(subprocess, 'check_output'):
        return subprocess.check_output(command, *args, **kwargs)
    else:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, *args, **kwargs)
        output = []
        while process.poll() is None:
            rlist, _, _ = select.select([process.stdout, process.stderr], [], [])
            for r in rlist:
                output.append(r.read())
        if process.returncode != 0:
            logger.err(''.join(output))
            raise Exception('Command \'{0}\' returned non-zero exit status {1}'.\
                    format(command, process.returncode))
        return ''.join(output)

def _short_ref(ref, length=8, *args, **kwargs):
    return ref[0:length]

def _generate_archive_name(logger, proj_name=None, ref_name=None, ref=None, build_id=None,
        *args, **kwargs):
    # Get git project name
    if proj_name is None:
        proj_name = os.path.basename(os.environ.get('CI_PROJECT_DIR', '<unknown>'))

    # Get git ref (e.g. tag, branch)
    if ref_name is None:
        ref_name = os.environ.get('CI_BUILD_REF_NAME', '<unknown>')

    # Get git ref (i.e. hash)
    if ref is None:
        ref = os.environ.get('CI_BUILD_REF', '<unknown>')

    # Get GitLab CI build ID
    if build_id is None:
        build_id = os.environ.get('CI_BUILD_ID', '<unknown>')

    arc_name = '{proj_name}_{ref_name}_{ref}_{build_id}.tar.gz'.format(
        proj_name = proj_name,
        ref_name = ref_name,
        ref = _short_ref(ref),
        build_id = build_id,
    )

    logger.debug('Generated archive name: {0}\n'.format(arc_name))

    return arc_name

def _cleanup(logger, git_dir, save_dir, cache_size, dry_run=False, *args, **kwargs):
    logger.log('Cleaning out the archive cache...\n', color=34)

    # Build a set of the latest commits for every ref
    latest_commits = set()
    output = _check_output(logger, ['git', 'for-each-ref'], cwd=git_dir)
    for match in re.finditer('^(?P<commit>\w+)', output, flags=re.MULTILINE):
        latest_commits.add(match.group('commit'))

    # Keep latest <cache_size> archives for each latest commit AND
    # the archive that was just generated
    current_arc = os.path.join(save_dir, _generate_archive_name(logger))
    logger.debug('Current archive: {0}\n'.format(current_arc))

    commit_and_build_id_regex = re.compile(r'^.*_(?P<commit>[^_]+)_(?P<id>\d+)\.tar\.gz')
    def get_build_id(archive):
        match = commit_and_build_id_regex.match(archive)
        return int(match.group('id'), 0) if match else 0

    # For each prefix, keep only the most recent <cache_size> archives, where most recent
    # is defined as those with the greatest build IDs
    for commit in latest_commits:
        logger.debug('Latest commit: {0}\n'.format(commit))

        # Get all archives for this commit, sorted in reverse order
        archives = sorted(glob.iglob(os.path.join(save_dir, _generate_archive_name(logger,
            proj_name='*', ref_name='*', ref=_short_ref(commit), build_id='*'))),
            key=get_build_id, reverse=True)

        # Delete all archives beyond cache size
        for arc in itertools.islice(archives, cache_size, None):
            if arc != current_arc:
                logger.log('Deleting {0}\n'.format(arc))
                if not dry_run:
                    os.remove(arc)

    # Get all saved archives
    archives = sorted(glob.iglob(os.path.join(save_dir,
        _generate_archive_name(logger, proj_name='*', ref_name='*', ref='*', build_id='*'))))

    def get_commit_id(archive):
        match = commit_and_build_id_regex.match(archive)
        return match.group('commit') if match else ''

    def is_latest_commit(commit):
        for latest in latest_commits:
            if commit == _short_ref(latest):
                return True
        return False

    # For each archive that hasn't been deleted, delete the ones that are not associated with
    # a latest commit, unless it's the current archive.
    for arc in archives:
        logger.debug('Archive: {0}\n'.format(arc))

        commit = get_commit_id(arc)
        logger.debug('Commit: {0}\n'.format(commit))

        if arc != current_arc and not is_latest_commit(commit):
            logger.log('Deleting {0}\n'.format(arc))
            if not dry_run:
                os.remove(arc)

    logger.log('Archive cache cleaned.\n', color=34)

def _store(logger, artifacts, git_dir, save_dir, dry_run=False, *args, **kwargs):
    # Create the save directory if it doesn't exist
    if not os.path.exists(save_dir) and not dry_run:
        os.mkdir(save_dir)

    # Build compressed tar file from artifacts
    name = os.path.join(save_dir, _generate_archive_name(logger))
    logger.log('Building archive...\n', color=34)
    if not dry_run:
        with contextlib.closing(tarfile.open(name, mode='w:gz')) as tar:
            for artifact in artifacts:
                arcname = artifact.split(git_dir)[1].lstrip(os.path.sep)
                logger.log('{0}\n'.format(arcname))
                tar.add(artifact, arcname=arcname)
    logger.log('Archive built.\n'.format(name), color=34)
    logger.log('{0}\n'.format(name))

def _excavate(logger, git_dir, dry_run=False, *args, **kwargs):
    # Run the git clean command to retrieve list of untracked files and dirs
    cmd = ['git', 'clean', '-ndx']
    output = _check_output(logger, cmd, cwd=git_dir)

    # Parse command's output for paths and build a list
    logger.log('Discovering artifacts...\n', color=34)
    artifacts = []
    for match in re.finditer(r'^\s*would\s+remove\s+(?P<path>.+)$\n', output,
            flags=re.IGNORECASE | re.MULTILINE):
        artifact = os.path.join(git_dir, match.group('path').strip())
        logger.log('{0}\n'.format(artifact))
        artifacts.append(artifact)

    return artifacts

def _parse_args(argv, *args, **kwargs):
    # Determine if the argparse module is available
    # If not, load optparse (Python 2.6)
    parser_class = None
    parser_class_kwargs = {'prog': __prog__, 'description': __description__}
    verbosity_kwargs = {}
    version_kwargs = {}

    try:
        import argparse
        parser_class = argparse.ArgumentParser
        add_argument_func = parser_class.add_argument
        verbosity_kwargs['choices'] = [0, 1, 2]
        version_kwargs['action'] = 'version'
        version_kwargs['version'] = '%(prog)s {}'.format(__version__)
    except ImportError:
        import optparse
        parser_class = optparse.OptionParser
        add_argument_func = parser_class.add_option
        def version_callback(option, opt, value, parser, *args, **kwargs):
            print '{0} {1}'.format(__prog__, __version__)
            raise SystemExit
        version_kwargs['action'] = 'callback'
        version_kwargs['callback'] = version_callback
        version_kwargs['help'] = 'show program\'s version number and exit'

    parser = parser_class(**parser_class_kwargs)

    add_argument_func(parser, '-V', '--version', **version_kwargs)
    add_argument_func(parser, '-q', '--quiet', action='store_true',
            help='suppress output, ignoring verbosity level')
    add_argument_func(parser, '-v', '--verbosity', type=int, default=1,
            help='output verbosity level', **verbosity_kwargs)
    add_argument_func(parser, '-g', '--git-directory', default=os.getcwd(),
            help='git working directory in which to perform excavation')
    add_argument_func(parser, '-s', '--save-directory',
            help='directory in which to store excavated build artifacts')
    add_argument_func(parser, '-c', '--save-cache-size', type=int, default=3,
            help='number of most recent builds per git ref for which to keep artifacts')
    add_argument_func(parser, '-n', '--dry-run', action='store_true',
            help='don\'t do anything -- just print what would be done')

    ret = parser.parse_args(argv)
    if isinstance(ret, tuple):
        parsed_args, _ = ret
    else:
        parsed_args = ret

    if parsed_args.quiet:
        parsed_args.verbosity = 0

    if parsed_args.save_directory is None:
        parsed_args.save_directory = '{0}_saved'.format(parsed_args.git_directory)

    return parsed_args

def main(argv=[], *args, **kwargs):
    """ Excutes the excavation process using argv as a list of command-line arguments. """

    # Parse arguments
    parsed_args = _parse_args(argv)

    # Setup logger
    logger = Logger(verbosity=parsed_args.verbosity)

    if parsed_args.dry_run:
        logger.log('Executing dry run...\n', color=33)

    # Discover build artifcats
    artifacts = _excavate(logger=logger, git_dir=parsed_args.git_directory,
            dry_run=parsed_args.dry_run)

    # Store artifacts in save directory
    _store(logger=logger, artifacts=artifacts, git_dir=parsed_args.git_directory,
            save_dir=parsed_args.save_directory, dry_run=parsed_args.dry_run)

    # Clean up old archives
    _cleanup(logger=logger, git_dir=parsed_args.git_directory,
            save_dir=parsed_args.save_directory, cache_size=parsed_args.save_cache_size,
            dry_run=parsed_args.dry_run)

    if parsed_args.dry_run:
        logger.log('Dry run complete.\n', color=33)

    return 0

if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
