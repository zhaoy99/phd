#!/usr/bin/env python3
#
# Download all of the OpenCL on GitHub (!)
#
# Shortcomings of this appraoch:
#
#   Only includes 'actual' OpenCL files, no inline strings
#
import json
import os
import re
import sys
import sqlite3

from time import sleep
from functools import partial

import requests

from argparse import ArgumentParser
from base64 import b64decode
from github import Github,GithubException

# Counters
repos_new_counter = 0
repos_modified_counter = 0
repos_unchanged_counter = 0
files_new_counter = 0
files_modified_counter = 0
files_unchanged_counter = 0
errors_counter = 0
status_string = ''

def print_repo_details(repo):
    print('url:', repo.url)
    print('owner:', repo.owner.email)
    print('name:', repo.name)
    print('fork:', repo.fork)
    print('stars:', repo.stargazers_count)
    print('contributors:', len([x for x in repo.get_contributors()]))
    print('forks:', repo.forks)
    print('created_at:', repo.created_at)
    print('updated_at:', repo.updated_at)


def print_file_details(file):
    print('url:', file.url)
    print('path:', file.path)
    print('sha:', file.sha)
    print('size:', file.size)


def print_counters():
    print('\r\033[Kfiles: new ', files_new_counter,
          ', modified ', files_modified_counter,
          ', unchanged ', files_unchanged_counter,
          '. repos: new ', repos_new_counter,
          ', modified ', repos_modified_counter,
          ', unchanged ', repos_unchanged_counter,
          '. errors ', errors_counter,
          '. current: ', status_string[0:25],
          sep='', end='')
    sys.stdout.flush()


def rate_limit(g):
    global status_string
    remaining = g.get_rate_limit().rate.remaining
    while remaining < 100:
        sleep(1)
        status_string = 'WAITING ON RATE LIMIT'
        print_counters()
        remaining = g.get_rate_limit().rate.remaining


def process_repo(g, db, repo):
    global repos_new_counter,repos_modified_counter,repos_unchanged_counter
    global status_string

    rate_limit(g)
    url = repo.url
    updated_at = str(repo.updated_at)
    name = repo.name
    status_string = name
    print_counters()

    c = db.cursor()
    c.execute("SELECT updated_at FROM Repositories WHERE url=?", (url,))
    cached_updated_at = c.fetchone()

    # Do nothing unless updated timestamps don't match
    if cached_updated_at and cached_updated_at[0] == updated_at:
        repos_unchanged_counter += 1
        return False

    owner = repo.owner.email
    fork = 1 if repo.fork else 0
    stars = repo.stargazers_count
    try:
        contributors = len([x for x in repo.get_contributors()])
    except GithubException as e:
        contributors = -1
    forks = repo.forks
    created_at = repo.created_at
    updated_at = repo.updated_at

    c.execute("DELETE FROM Repositories WHERE url=?", (url,))
    c.execute("INSERT INTO Repositories VALUES(?,?,?,?,?,?,?,?,?)",
              (url, owner, name, fork, stars, contributors, forks, created_at,
               updated_at))

    if cached_updated_at:
        repos_modified_counter += 1
    else:
        repos_new_counter += 1
    db.commit()
    return True


def is_opencl_path(path):
    return path.endswith('.cl') or path.endswith('.ocl')

_include_re = re.compile('\w*#include ["<](.*)[">]')

def download_file(github_token, repo, url, stack):
    # Recursion stack
    stack.append(url)

    response = json.loads(requests.get(
        url,
        headers = {
            'Authorization': 'token ' + str(github_token)
        }
    ).content.decode('utf-8'))
    src = b64decode(response['content']).decode('utf-8')

    outlines = []
    for line in src.split('\n'):
        match = re.match(_include_re, line)
        if match:
            include_name = match.group(1)

            # Try and resolve relative paths
            include_name = include_name.replace('../', '')

            branch = repo.default_branch
            tree_iterator = repo.get_git_tree(branch, recursive=True).tree
            include_url = ''
            for f in tree_iterator:
                if f.path.endswith(include_name):
                    include_url = f.url
                    break

            if include_url and include_url not in stack:
                include_src = download_file(github_token, repo, include_url)
                outlines.append(include_src)
            else:
                if not include_url:
                    outlines.append('// [FETCH] didnt find: ' + line)
                else:
                    outlines.append('// [FETCH] skipped: ' + line)
        else:
            outlines.append(line)

    return '\n'.join(outlines)


def process_file(g, github_token, db, repo, file):
    global files_new_counter,files_modified_counter,files_unchanged_counter
    global status_string

    # rate_limit(g)

    # We're only interested in OpenCL files.
    if not is_opencl_path(file.path):
        return

    url = file.url
    sha = file.sha
    path = file.path
    status_string = repo.name + '/' + path
    print_counters()

    c = db.cursor()
    c.execute("SELECT sha FROM ContentMeta WHERE id=?", (url,))
    cached_sha = c.fetchone()

    # Do nothing unless checksums don't match
    if cached_sha and cached_sha[0] == sha:
        files_unchanged_counter += 1
        return False

    repo_url = repo.url
    contents = download_file(github_token, repo, file.url, [])
    size = file.size

    c.execute("DELETE FROM ContentFiles WHERE id=?", (url,))
    c.execute("DELETE FROM ContentMeta WHERE id=?", (url,))
    c.execute("INSERT INTO ContentFiles VALUES(?,?)",
              (url, contents))
    c.execute("INSERT INTO ContentMeta VALUES(?,?,?,?,?)",
              (url, path, repo_url, sha, size))

    if cached_sha:
        files_modified_counter += 1
    else:
        files_new_counter += 1

    db.commit()
    return True

def main():
    global errors_counter

    parser = ArgumentParser()
    parser.add_argument('input', help='path to SQL input dataset')
    args = parser.parse_args()
    db_path = args.input

    try:
        github_username = os.environ['GITHUB_USERNAME']
        github_pw = os.environ['GITHUB_PW']
        github_token = os.environ['GITHUB_TOKEN']
    except KeyError as e:
        print('fatal: environment variable {} not set'.format(e))
        sys.exit(1)

    g = Github(github_username, github_pw)
    db = sqlite3.connect(db_path)
    handle_repo = partial(process_repo, g, db)
    # TODO: Verify tables have been created

    # Fetch the repositories to iterate over. Since opencl isn't
    # treated as a first-class language by GitHub, we can't use the
    # 'language=' keyword for queries, so instead we through a much
    # wider net and filter the results afterwards.
    #
    query_terms = [
        'opencl',
        'cl',
        'khronos',
        'gpu',
        'gpgpu',
        'cuda',
        'amd',
        'nvidia',
        'heterogeneous'
    ]
    for query in query_terms:
        repos = g.search_repositories(query + ' fork:true sort:stars')

        for repo in repos:
            repo_modified = handle_repo(repo)

            # Do nothing unless the repo is new / modified
            if not repo_modified:
                continue

            handle_file = partial(process_file, g, github_token, db, repo)

            # Iterate over the entire git tree of the repo's default
            # branch (usually 'master'). If a file ends with the .cl
            # extension, check to see if we already have it, else download
            # it.
            #
            try:
                branch = repo.default_branch
                tree_iterator = repo.get_git_tree(branch, recursive=True).tree
                for f in tree_iterator:
                    try:
                        handle_file(f)
                    except Exception as e:
                        errors_counter += 1
            except GithubException as e:
                # Do nothing in case of error (such as an empty repo)
                pass

    print_counters()
    print("\n\ndone.")
    db.close()


if __name__ == '__main__':
    main()