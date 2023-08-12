import logging
import logging.handlers
import sys
import time
import click
import click_log
import shutil
import datetime
import os
import zipfile
import requests
import io
import tempfile
import platform
from pathlib import Path
import atexit
import subprocess
from typing import List, Literal, Optional

from . import (
    utils,
    templates,
    core,
    constants,
    VERSION,
    CORE_COMMIT,
)


from .project import Project, Format

log = logging.getLogger("ptxlogger")
click_log.basic_config(log)
click_log_format = click_log.ColorFormatter()
# create memory handler which displays error and critical messages at the end as well.
sh = logging.StreamHandler(sys.stderr)
sh.setFormatter(click_log_format)
mh = logging.handlers.MemoryHandler(
    capacity=1024 * 100, flushLevel=100, target=sh, flushOnClose=False
)
mh.setLevel(logging.ERROR)
log.addHandler(mh)

# Call exit_command() at close to handle errors encountered during run.
atexit.register(utils.exit_command, mh)

CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])


#  Click command-line interface
@click.group(invoke_without_command=True, context_settings=CONTEXT_SETTINGS)
@click.pass_context
# Allow a verbosity command:
@click_log.simple_verbosity_option(
    log,
    help="Sets the severity of log messaging: DEBUG for all, INFO (default) for most, then WARNING, ERROR, and CRITICAL for decreasing verbosity.",
)
@click.version_option(VERSION, message=VERSION)
@click.option(
    "-t",
    "--targets",
    is_flag=True,
    help='Display list of build/view "targets" available in the project manifest.',
)
def main(ctx: click.Context, targets: bool) -> None:
    """
    Command line tools for quickly creating, authoring, and building PreTeXt projects.

    PreTeXt Guide for authors and publishers:

    - https://pretextbook.org/documentation.html

    PreTeXt CLI README for developers:

    - https://github.com/PreTeXtBook/pretext-cli/

    Use the `--help` option on any CLI command to learn more, for example,
    `pretext build --help`.
    """
    if (pp := utils.project_path()) is not None:
        if targets:
            for target in Project.parse(pp).target_names():
                print(target)
            return
        # create file handler which logs even debug messages
        fh = logging.FileHandler(pp / "cli.log", mode="w")
        fh.setLevel(logging.DEBUG)
        file_log_format = logging.Formatter("{levelname:<8}: {message}", style="{")
        fh.setFormatter(file_log_format)
        log.addHandler(fh)
        # output info
        log.info(f"PreTeXt project found in `{utils.project_path()}`.")
        # permanently change working directory for rest of process
        os.chdir(pp)
        if utils.requirements_version() is None:
            log.warning(
                "Project's CLI version could not be detected from `requirements.txt`."
            )
            log.warning("Try `pretext init --refresh` to produce a compatible file.")
        elif utils.requirements_version() != VERSION:
            log.warning(f"Using CLI version {VERSION} but project's `requirements.txt`")
            log.warning(
                f"is configured to use {utils.requirements_version()}. Consider either installing"
            )
            log.warning(
                f"CLI version {utils.requirements_version()} or changing `requirements.txt` to match {VERSION}."
            )
        else:
            log.debug(
                f"CLI version {VERSION} matches requirements.txt {utils.requirements_version()}."
            )
    else:
        log.info("No existing PreTeXt project found.")
    if ctx.invoked_subcommand is None:
        log.info("Run `pretext --help` for help.")


# pretext support
@main.command(
    short_help="Use when communicating with PreTeXt support.",
    context_settings=CONTEXT_SETTINGS,
)
def support() -> None:
    """
    Outputs useful information about your installation needed by
    PreTeXt volunteers when requesting help on the pretext-support
    Google Group.
    """
    log.info("")
    log.info("Please share the following information when posting to the")
    log.info("pretext-support Google Group.")
    log.info("")
    log.info(f"PreTeXt-CLI version: {VERSION}")
    log.info(f"    PyPI link: https://pypi.org/project/pretextbook/{VERSION}/")
    log.info(f"PreTeXt core resources commit: {CORE_COMMIT}")
    log.info(f"Runestone Services version: {core.get_runestone_services_version()}")
    log.info(f"OS: {platform.platform()}")
    log.info(f"Python version: {platform.python_version()}")
    log.info(f"Current working directory: {Path().resolve()}")
    if utils.project_path() is not None:
        log.info(f"PreTeXt project path: {utils.project_path()}")
        log.info("")
        log.info("Contents of project.ptx:")
        log.info("------------------------")
        log.info(utils.project_xml_string())
        log.info("------------------------")

        # Create a project from the project.ptx file
        project = Project.parse()
        project.init_core()

        for exec_name in project.get_executables().dict():
            if utils.check_executable(exec_name) is None:
                log.warning(
                    f"Unable to locate the command for <{exec_name}> on your system."
                )
    else:
        log.info("No project.ptx found.")


# pretext devscript
@main.command(
    short_help="Alias for the developer pretext/pretext script.",
    context_settings={"help_option_names": [], "ignore_unknown_options": True},
)
@click.argument("args", nargs=-1)
def devscript(args: List[str]) -> None:
    """
    Aliases the core pretext script.
    """
    PY_CMD = sys.executable
    subprocess.run(
        [PY_CMD, str(core.resources.path("pretext", "pretext"))] + list(args)
    )


# pretext new
@main.command(
    short_help="Generates the necessary files for a new PreTeXt project.",
    context_settings=CONTEXT_SETTINGS,
)
@click.argument(
    "template",
    default="book",
    type=click.Choice(constants.NEW_TEMPLATES, case_sensitive=False),
)
@click.option(
    "-d",
    "--directory",
    type=click.Path(),
    default="new-pretext-project",
    help="Directory to create/use for the project.",
)
@click.option(
    "-u",
    "--url-template",
    type=click.STRING,
    help="Download a zipped template from its URL.",
)
def new(template: str, directory: Path, url_template: str) -> None:
    """
    Generates the necessary files for a new PreTeXt project.
    Supports `pretext new book` (default) and `pretext new article`,
    or generating from URL with `pretext new --url-template [URL]`.
    """
    directory_fullpath = Path(directory).resolve()
    if utils.project_path(directory_fullpath) is not None:
        log.warning(
            f"A project already exists in `{utils.project_path(directory_fullpath)}`."
        )
        log.warning("No new project will be generated.")
        return
    log.info(
        f"Generating new PreTeXt project in `{directory_fullpath}` using `{template}` template."
    )
    if url_template is not None:
        r = requests.get(url_template)
        archive = zipfile.ZipFile(io.BytesIO(r.content))
    else:
        with templates.resource_path(f"{template}.zip") as template_path:
            archive = zipfile.ZipFile(template_path)
    # find (first) project.ptx to use as root of template
    filenames = [Path(filepath).name for filepath in archive.namelist()]
    project_ptx_index = filenames.index("project.ptx")
    project_ptx_path = Path(archive.namelist()[project_ptx_index])
    project_dir_path = project_ptx_path.parent
    with tempfile.TemporaryDirectory() as tmpdirname:
        for filepath in [
            filepath
            for filepath in archive.namelist()
            if project_dir_path in Path(filepath).parents
        ]:
            archive.extract(filepath, path=tmpdirname)
        tmpsubdirname = Path(tmpdirname) / project_dir_path
        shutil.copytree(tmpsubdirname, directory, dirs_exist_ok=True)
    # generate requirements.txt
    with open(directory_fullpath / "requirements.txt", "w") as f:
        f.write(f"pretext == {VERSION}")
    log.info(
        f"Success! Open `{directory_fullpath}/source/main.ptx` to edit your document"
    )
    log.info(
        f"Then try to `pretext build` and `pretext view` from within `{directory_fullpath}`."
    )


# pretext init
@main.command(
    short_help="Generates the project manifest for a PreTeXt project in the current directory.",
    context_settings=CONTEXT_SETTINGS,
)
@click.option(
    "-r",
    "--refresh",
    is_flag=True,
    help="Refresh initialization of project even if project.ptx exists.",
)
def init(refresh: bool) -> None:
    """
    Generates the project manifest for a PreTeXt project in the current directory. This feature
    is mainly intended for updating existing projects to use this CLI.

    If --refresh is used, files will be generated even if the project has already been initialized.
    Existing files won't be overwritten; a copy of the fresh initialized file will be created
    with a timestamp in its filename for comparison.
    """
    if utils.project_path() is not None and not refresh:
        log.warning(f"A project already exists in `{utils.project_path()}`.")
        log.warning(
            "Use `pretext init --refresh` to refresh initialization of an existing project."
        )
        return
    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    resource_to_dest = {
        "project.ptx": "project.ptx",
        "publication.ptx": "publication/publication.ptx",
        ".gitignore": ".gitignore",
        ".devcontainer.json": ".devcontainer.json",
    }
    for resource in resource_to_dest:
        with templates.resource_path(resource) as resource_path:
            project_resource_path = Path(resource_to_dest[resource]).resolve()
            if project_resource_path.exists():
                new_resource_name = (
                    project_resource_path.stem
                    + "-"
                    + timestamp
                    + project_resource_path.suffix
                )
                project_resource_path = project_resource_path.parent / new_resource_name
                log.warning(
                    f"You already have a {resource} file; a new default one for comparison has been created as {project_resource_path}."
                )
            log.info(f"Generated `{project_resource_path}`\n")
            if not project_resource_path.parent.exists():
                project_resource_path.parent.mkdir()
            shutil.copyfile(resource_path, project_resource_path)
    # Create requirements.txt
    requirements_path = Path("requirements.txt").resolve()
    if requirements_path.exists():
        requirements_path = Path(f"requirements-{timestamp}.txt").resolve()
        log.warning(
            f"You already have a requirements.txt file at {Path('requirements.txt').resolve()}`."
        )
        log.warning(
            f"The one suggested by PreTeXt will be created as {requirements_path} for comparison."
        )
    with open(requirements_path, "w") as f:
        f.write(f"pretext == {VERSION}")
    log.info(f"Generated requirements file at {requirements_path}.\n")

    log.info("Success! Open project.ptx to edit your project manifest.")
    log.info(
        "Edit your <target/>s to point to the location of your PreTeXt source files."
    )


# pretext build
@main.command(short_help="Build specified target", context_settings=CONTEXT_SETTINGS)
@click.argument("target_name", required=False, metavar="target")
@click.option(
    "--clean",
    is_flag=True,
    help="Destroy output's target directory before build to clean up previously built files",
)
@click.option(
    "-g",
    "--generate",
    is_flag=False,
    flag_value="ALL",
    default=None,
    type=click.Choice(constants.ASSETS, case_sensitive=False),
    help="Force (re)generates assets for target.  -g [asset] will generate the specific assets given.",
)
@click.option(
    "-q",
    "--no-generate",
    is_flag=True,
    default=False,
    help="Do not generate assets for target, even if their source has changed since last build.",
)
@click.option(
    "-x",
    "--xmlid",
    type=click.STRING,
    help="xml:id of the root of the subtree to be built.",
)
def build(
    target_name: str,
    clean: bool,
    generate: str,
    no_generate: bool,
    xmlid: Optional[str],
) -> None:
    """
    Build [TARGET] according to settings specified by project.ptx.

    If using elements that require separate generation of assets (e.g., webwork, latex-image, etc.) then these will be generated automatically if their source has changed since the last build.  You can suppress this with the `--no-generate` flag, or force a regeneration with the `--generate` flag.

    Certain builds may require installations not included with the CLI, or internet
    access to external servers. Command-line paths
    to non-Python executables may be set in project.ptx. For more details,
    consult the PreTeXt Guide: https://pretextbook.org/documentation.html
    """

    # Set up project and target based on command line arguments and project.ptx

    # Supply help if not in project subfolder
    if utils.no_project(task="build"):
        return
    # Create a new project, apply overlay, and get target. Note, the CLI always finds changes to the root folder of the project, so we don't need to specify a path to the project.ptx file.
    project = Project.parse()
    # Now create the target if the target_name is not missing.
    try:
        target = project.get_target(name=target_name)
    except AssertionError as e:
        utils.show_target_hints(target_name, project, task="build")
        log.critical("Exiting without completing build.")
        log.debug(e, exc_info=True)
        return

    # Call generate if flag is set
    if generate:
        try:
            target.generate_assets(only_changed=False, xmlid=xmlid)
        except Exception as e:
            log.debug(f"Failed to generate assets: {e}", exc_info=True)
    # Call build
    try:
        log.debug(f"Building target {target.name} with root of tree below {xmlid}")
        target.build(clean=clean, no_generate=no_generate, xmlid=xmlid)
        log.info("\nSuccess! Run `pretext view` to see the results.\n")
    except Exception as e:
        log.critical(e)
        log.debug("Exception info:\n##################\n", exc_info=True)
        log.info("##################")
        sys.exit("Failed to build.  Exiting...")


# pretext generate
@main.command(
    short_help="Generate specified assets for default target or targets specified by `-t`",
    context_settings=CONTEXT_SETTINGS,
)
@click.argument(
    "assets", type=click.Choice(constants.ASSETS, case_sensitive=False), nargs=-1
)
@click.option(
    "-t",
    "--target",
    "target_name",
    type=click.STRING,
    help="Name of target to generate assets for (if not specified, first target from manifest is used).",
)
@click.option(
    "-x", "--xmlid", type=click.STRING, help="xml:id of element to be generated."
)
@click.option(
    "-q",
    "--only-changed",
    is_flag=True,
    default=False,
    help="Limit generation of assets to only those that have changed since last call to pretext.",
)
@click.option(
    "--all-formats",
    is_flag=True,
    default=False,
    help="Generate all possible asset formats rather than just the defaults for the specified target.",
)
def generate(
    assets: List[str],
    target_name: Optional[str],
    all_formats: bool,
    only_changed: bool,
    xmlid: Optional[str],
) -> None:
    """
    Generate specified (or all) assets for the default target (first target in "project.ptx"). Asset "generation" is typically
    slower and performed less frequently than "building" a project, but is
    required for many PreTeXt features such as webwork and latex-image.

    Certain assets may require installations not included with the CLI, or internet
    access to external servers. Command-line paths
    to non-Python executables may be set in project.ptx. For more details,
    consult the PreTeXt Guide: https://pretextbook.org/documentation.html
    """

    # If no assets are given as arguments, then assume 'ALL'
    if assets == ():
        assets = ["ALL"]

    if utils.no_project(task="generate assets for"):
        return

    project = Project.parse()
    # Now create the target if the target_name is not missing.
    try:
        target = project.get_target(name=target_name)
    except AssertionError as e:
        utils.show_target_hints(target_name, project, task="generating assets for")
        log.critical("Exiting without completing build.")
        log.debug(e, exc_info=True)
        return

    try:
        f'Generating assets in for the target "{target.name}".'
        target.generate_assets(
            specified_asset_types=assets,
            all_formats=all_formats,
            only_changed=only_changed,  # Unless requested, generate all assets, so don't check the cache.
            xmlid=xmlid,
        )
        log.info("Finished generating assets.\n")
    except Exception as e:
        log.critical(e)
        log.debug("Exception info:\n##################\n", exc_info=True)
        log.info("##################")
        sys.exit("Generating assets as failed.  Exiting...")


# pretext view
@main.command(
    short_help="Preview specified target based on its format.",
    context_settings=CONTEXT_SETTINGS,
)
@click.argument("target_name", metavar="target", required=False)
@click.option(
    "-a",
    "--access",
    type=click.Choice(["public", "private"], case_sensitive=False),
    default="private",
    show_default=True,
    help="""
    If running a local server,
    choose whether or not to allow other computers on your local network
    to access your documents using your IP address.
    """,
)
@click.option(
    "-p",
    "--port",
    type=click.INT,
    help="""
    If running a local server,
    choose which port to use.
    (Ignored when used
    in CoCalc, which works automatically.)
    """,
)
@click.option(
    "-b",
    "--build",
    is_flag=True,
    help="""
    Run a build before viewing.
    """,
)
@click.option(
    "-g",
    "--generate",
    is_flag=True,
    help="Generate all assets before viewing",
)
@click.option(
    "--no-launch",
    is_flag=True,
    help="By default, pretext view tries to launch the default application to view the specified target.  Setting this suppresses this behavior.",
)
@click.option(
    "-r",
    "--restart-server",
    is_flag=True,
    default=False,
    help="Force restart the local http server in case it is already running.",
)
@click.option(
    "-s",
    "--stop-server",
    is_flag=True,
    default=False,
    help="Stop the local http server if running.",
)
def view(
    target_name: str,
    access: Literal["public", "private"],
    port: Optional[int],
    build: bool,
    generate: Optional[str],
    no_launch: bool,
    restart_server: bool,
    stop_server: bool,
) -> None:
    """
    Starts a local server to preview built PreTeXt documents in your browser.
    TARGET is the name of a <target/> defined in `project.ptx` (defaults to the first target).

    After running this command, you can switch to a new terminal to rebuild your project and see the changes automatically reflected in your browser.

    If a server is already running, no new server will be started (nor will it need to be), unless you pass the `--restart-server` flag. You can stop a running server with CTRL+C or by passing the `--stop-server` flag.
    """

    # pretext view -s should immediately stop the server and do nothing else.
    if stop_server:
        log.info("\nStopping server.")
        utils.stop_server()
        return
    if utils.no_project(task="view the output for"):
        return
    project = Project.parse()
    try:
        target = project.get_target(name=target_name)
    except AssertionError as e:
        utils.show_target_hints(target_name, project, task="view")
        log.critical("Exiting.")
        log.debug(e, exc_info=True)
        return

    # Call generate if flag is set
    if generate:
        try:
            target.generate_assets(only_changed=False)
        except Exception as e:
            log.info(f"Failed to generate assets: {e}")
            log.debug("", exc_info=True)
    # Call build if flag is set
    if build:
        try:
            target.build()
        except Exception as e:
            log.info(f"Failed to build: {e}")
            log.debug("Exception info:\n##################\n", exc_info=True)
    # Start server if there isn't one running already:
    used_port = utils.server_is_running()
    if port or restart_server or not used_port:
        # First terminate the running server
        if used_port:
            utils.stop_server(used_port)
        # Start the server
        log.info("Starting server.")
        server = project.server_process(
            output_dir=target.output_dir_abspath(),
            access=access,
            port=port or 8128,
            launch=not no_launch,
        )
        server.start()
        try:
            while server.is_alive():
                time.sleep(1)
        except KeyboardInterrupt:
            log.info("Stopping server.")
            server.terminate()
            return
    else:
        url = (
            "http://localhost:"
            + str(used_port)
            + target.output_dir_abspath()
            .as_posix()
            .replace(project.abspath().as_posix(), "")
        )
        log.info(f"Viewing output for {target.name} at {url}")


# pretext deploy
@main.command(
    short_help="Deploys Git-managed project to GitHub Pages.",
    context_settings=CONTEXT_SETTINGS,
)
@click.argument("target_name", metavar="target", required=False)
@click.option("-u", "--update_source", is_flag=True, required=False)
def deploy(target_name: str, update_source: bool) -> None:
    """
    Automatically deploys most recent build of [TARGET] to GitHub Pages,
    making it available to the general public.
    Requires that your project is under Git version control
    properly configured with GitHub and GitHub Pages. Deployed
    files will live in the gh-pages branch of your repository.
    """
    if utils.no_project(task="deploy"):
        return
    project = Project.parse()
    target = project.get_target(name=target_name)
    if target.format != Format.HTML:
        log.critical("Target could not be found in project.ptx manifest.")
        # only list targets with html format.
        log.critical(
            f"Possible HTML targets to deploy are: {project.target_names(Format.HTML)}"
        )
        log.critical("Exiting without completing task.")
        return
    project.deploy(target_name, update_source)
