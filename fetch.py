#!/usr/bin/env python3

"""A simple CI script, pulling from the CC repo and building specific configurations"""

import configparser, sys, os.path, argparse, subprocess, urllib.request, json, re, zipfile, string, pystache, hashlib

extension = 'json'
import json as cfg

# extension = 'yaml'
# import yaml as cfg

def log(msg, *args):
    "Print a format string to the console"
    print("\033[32m" + msg % tuple(args) + "\033[0m")

def load_config(name):
    "Load configuration options from a file"
    if os.path.isfile(name):
        with open(name, 'r') as file:
            config = cfg.load(file)
    else:
        config = {
            'path': "ComputerCraft",
            'output': 'out',
            'html-out': 'index.html',
            'html-url': 'out/',
            'cache': 'c3i-cache.json',
            'additional': [ ],
            'recommended': ['default']
        }
        with open(name, 'w') as file:
            cfg.dump(config, file, indent=2)
            file.write('\n')

    config['path'] = os.path.abspath(config['path'])
    config['cache'] = os.path.abspath(config['cache'])
    config['output'] = os.path.abspath(config['output'])
    config['html-out'] = os.path.abspath(config['html-out'])
    config['html-resources'] = { name: os.path.abspath(path)
                                 for (name, path) in config['html-resources'].items() }

    return config

def load_cache(name):
    """Attempt to load the build cache information"""
    if os.path.isfile(name):
        with open(name, 'r') as file:
            data = json.load(file)
        return data['refs'], data['configurations']
    else:
        return {}, {}

def get_prs():
    """Load all open PRs from the ComputerCraft repo"""
    out = []

    url = 'https://api.github.com/repos/dan200/computercraft/pulls?state=open'
    while url:
        with urllib.request.urlopen(url) as file:
            data = json.load(file)

            out.extend({ 'branch': x['head']['ref'],
                         'desc': 'PR #%d: %s' % (x['number'], x['title']),
                         'link': x['html_url'],
                         'repo': x['head']['repo']['clone_url'] if x['head']['repo'] else None,
                         'name': x['head']['repo']['full_name'] if x['head']['repo'] else x['head']['user']['login'] + '/ComputerCraft'
            } for x in data)

            if 'Link' in file.headers:
                match = re.search(r'<([^>]+)>; rel="next"', file.headers['Link'])
                url = match and match.group(1)
            else:
                url = None

    return out

def init_repo(path, url):
    """Move into the given repo, cloning it if required"""
    if os.path.exists(path):
        if not os.path.isdir(path):
            raise Exception(path + " is not a directory")
    else:
        log("Cloning %s into %s", url, path)
        subprocess.check_call(["git", "clone", "-q", url, path])

    os.chdir(path)

def init_remotes(path, prs):
    """Sync the remotes in the given repo with the open prs"""
    config = configparser.ConfigParser()
    config.read(os.path.join(path, ".git", "config"))

    remotes = {}
    new_remotes = { pr['name']: pr['repo'] for pr in prs if pr['repo'] }
    for section in config.sections():
        match = re.fullmatch('remote "([^"]+)"', section)
        if match:
            name = match.group(1)
            remotes[match.group(1)] = config[section]['url']

    for name, repo in new_remotes.items():
        if name not in remotes:
            log("Adding %s => %s", name, repo)
            subprocess.check_call(["git", "remote", "add", name, repo])

def get_refs(repo, branches):
    """Get a map of branch name to SHAs."""
    branch_refs = {}
    for branch in branches:
        whole_path = os.path.join(repo, ".git", "refs", "remotes", branch)
        if os.path.isfile(whole_path):
            with open(whole_path, "r") as handle:
                branch_refs[branch] = handle.read().strip()

    packed_refs = os.path.join(repo, ".git", "packed-refs")
    if os.path.isfile(packed_refs):
        with open(packed_refs, 'r') as handle:
            for line in handle:
                if not line.startswith("#"):
                    [ref, branch] = line.split(" ", 1)
                    if branch.startswith("refs/remotes/"):
                        branch = branch.replace("refs/remotes/", "", 1).strip()
                        if branch in branches and branch not in branch_refs:
                            branch_refs[branch] = ref.strip()

    return branch_refs

def get_commit(repo, sha):
    """Get the commit message from a given SHA."""
    data = subprocess.check_output(["git", "show", "-s", "--format=medium", sha]).decode("utf-8").split("\n")

    lines = []
    out = { 'Message': lines }
    for line in data:
        if line.startswith("commit"):
            out['Commit'] = line.split(" ", 1)[1]
        elif line.startswith("    "):
            lines.append(line[4:])
        elif ':' in line:
            [field, data] = line.split(":", 1)
            out[field.strip()] = data.strip()
    return out

def build_version(configuration, name, *additional):
    if name == "default":
        full_name = 'ComputerCraft'
    else:
        full_name = 'ComputerCraft-' + name
    full_version = '%s-build%d' % (configuration['main_version'], configuration['version'])

    out = []
    for (branch, ref) in configuration['refs'].items():
        out.append({
            'name': branch,
            'sha': ref,
            'sha_short': ref[0:8],
            'msg': get_commit(config['path'], ref)['Message'][0],
        })

    # TODO: Sort so origin/master is on the top
    out.sort(key = lambda x: x['name'])

    out = {
        **configuration,
        'root_folder': 'dan200/computercraft/%s/' % (full_name),
        'file': "%s/%s-%s.jar" % (full_version, full_name, full_version),
        'refs': out,
    }

    for overrides in additional:
        for entry in overrides:
            if entry['name'] == name:
                out.update(entry)

    return out

def hash_file(path):
    hash = hashlib.sha256()
    with open(path, "rb") as file:
        hash.update(file.read())
    return hash.hexdigest()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="A CI script for ComputerCraft, building specified configurations")
    parser.add_argument('--force', '-f',
                        help='whether to force a build',
                        default=False, action='store_true')
    parser.add_argument('--no-update', '-U',
                        help='whether to avoid updating the remotes.',
                        default=False, action='store_true')
    parser.add_argument('--no-build', '-B',
                        help='whether to avoid building anything.',
                        default=False, action='store_true')
    parser.add_argument('--no-prs', '-P',
                        help='whether to avoid fetching PRs.',
                        default=False, action='store_true')
    parser.add_argument('--config',
                        help='the config file to use',
                        default='c3i.'+extension)
    parser.add_argument('--dump', metavar='PATH',
                        help='dump the resulting data to a JSON file')

    args = parser.parse_args()

    # Load our config file
    config = load_config(args.config)

    # Setup our templating system
    renderer = pystache.Renderer(search_dirs = [os.path.abspath("template")])

    # Setup the file to dump to
    dump = os.path.abspath(args.dump) if args.dump != None else None

    # Fetch the existing PRs
    prs = [] if args.no_prs else get_prs()

    # Load the configuration cache
    old_refs, configuration_cache = load_cache(config['cache'])

    # Setup the repo
    init_repo(config['path'], "https://github.com/dan200/ComputerCraft.git")
    init_remotes(config['path'], prs)

    # We reset anything we may have done before
    log("Cleaning the existing tree")
    subprocess.call(["git", "checkout", "-q", "--", "."])
    subprocess.call(["git", "checkout", "-q", "master"])
    subprocess.call(["git", "branch", "-q", "-D", "temp_branch"])

    # Gather a list of configurations we need to build
    configurations = [ { 'name': 'default', 'desc': 'ComputerCraft', 'branches': [] } ]
    if 'additional' in config:
        configurations.extend(config['additional'])
    for pr in prs:
        configurations.append({ 'name': pr['name'].replace('/', '-') + '-' + pr['branch'].replace('/', '-'),
                                'desc': pr['desc'],
                                'pr':   pr['link'],
                                'branches': [pr['name'] + '/' + pr['branch'] ]})

    # Gather a set of branches we're interested in
    branches = {"origin/master"}
    for build in configurations:
        branches.update(build['branches'])

    # Update the remotes
    if not args.no_update:
        log("Fetching remotes")
        subprocess.run(["git", "remote", "update", "--prune"])

    # Get a list of all new remotes
    new_refs = get_refs(config['path'], branches)

    # Computer which branches have changed
    delta = set()
    for (branch, ref) in new_refs.items():
        if branch not in old_refs or old_refs[branch] != ref:
            delta.add(branch)

    # Now let's attempt to rebuild everything
    for build in configurations:
        name = build['name']

        # If none of the branches have changed and we've built it before, then skip!
        if args.no_build or (not args.force and "origin/master" not in delta and not any(map(delta.__contains__, build['branches'])) and name in configuration_cache):
            continue

        missing = list(filter(lambda x: x not in new_refs, build['branches']))
        if len(missing) > 0:
            log("Missing %s for %s", ', '.join(missing), name)
            continue

        # We uniquely number each version
        log("Rebuilding %s (using %s)", name, ', '.join(build['branches']))

        try:
            # Attempt to merge each branch
            subprocess.check_call(["git", "checkout", "-q", "-b", "temp_branch", "origin/master"])
            for branch in build['branches']:
                subprocess.check_call(["git", "merge", "--no-edit", "-q", branch])

            # Then build ComputerCraft
            subprocess.check_call(["./gradlew", "-q", "clean", "build"])

            # Setup various file names
            files = os.scandir(os.path.join(config['path'], 'build', 'libs'))
            file = next(file for file in files if "-sources.jar" not in file.path)

            main_version = file.name.replace('ComputerCraft', '').replace('.jar', '').strip('-')

            if name in configuration_cache and configuration_cache[name]['main_version'] == main_version:
                version = configuration_cache[name]['version'] + 1
            else:
                version = 0

            if name == "default":
                full_name = 'ComputerCraft'
            else:
                full_name = 'ComputerCraft-' + name

            full_version = "%s-build%d" % (main_version, version)

            # Package the LuaJ jar. This isn't always needed, but honestly this is the easiest way to achieve this.
            luaj = os.path.join(config['path'], 'libs', 'luaj-jse-2.0.3.jar')
            if os.path.isfile(luaj):
                with zipfile.ZipFile(luaj, 'r') as luaj_zip:
                    with zipfile.ZipFile(file.path, 'a') as cc_zip:
                        for entry in luaj_zip.infolist():
                            if not entry.filename.startswith("META-INF") and not entry.is_dir():
                                cc_zip.writestr(entry, luaj_zip.read(entry))

            # And plonk it in maven
            output = os.path.join(config['output'], 'dan200', 'computercraft', full_name, full_version)
            os.makedirs(output, exist_ok=True)
            os.rename(file.path, os.path.join(output, '%s-%s.jar' % (full_name, full_version)))


            with open(os.path.join(output, '%s-%s.pom' % (full_name, full_version)), 'w') as file:
                file.write(
"""<?xml version="1.0" encoding="UTF-8"?>
<project xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 http://maven.apache.org/xsd/maven-4.0.0.xsd" xmlns="http://maven.apache.org/POM/4.0.0"
    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <modelVersion>4.0.0</modelVersion>
  <groupId>dan200.computercraft</groupId>
  <artifactId>%s</artifactId>
  <version>%s</version>
</project>
""" % (full_name, full_version))

            refs = { branch: new_refs[branch] for branch in build['branches'] }
            refs['origin/master'] = new_refs['origin/master']
            configuration_cache[name] = {
                'version': version,
                'main_version': main_version,
                'refs': refs,
            }

            ## TODO: REMOVE THIS
            with open(config['cache'], 'w') as file:
                json.dump({ 'refs': new_refs, 'configurations': configuration_cache }, file)

        except subprocess.CalledProcessError as e:
            log("%s exited with %d", e.cmd, e.returncode)
            subprocess.call(["git", "merge", "--abort"])

        # And clear everything
        subprocess.call(["git", "checkout", "-q", "--", "."])
        subprocess.check_call(["git", "checkout", "-q", "master"])
        subprocess.check_call(["git", "branch", "-q", "-D", "temp_branch"])

    # Write the cache again
    log("Writing cache")
    with open(config['cache'], 'w') as file:
        json.dump({ 'refs': new_refs, 'configurations': configuration_cache }, file)

    result = {
        **config,
        'html-resources': { name: hash_file(path)
                            for (name, path) in config['html-resources'].items() },
        'recommended': [ build_version(configuration_cache[c['name']], c['name'], configurations, config['overrides'], config['recommended'])
                         for c in config['recommended']
                         if c['name'] in configuration_cache ],
        'all':         [ build_version(configuration_cache[c['name']], c['name'], configurations, config['overrides'])
                         for c in configurations
                         if c['name'] in configuration_cache ],
    }

    # Write a JSON dump
    if dump != None:
        log("Writing JSON")
        with open(dump, "w") as file:
            json.dump(result, file)

    # Write a HTML download page.
    log("Writing HTML")
    with open(config['html-out'], 'w') as file:
        file.write(renderer.render_name("main", result))
