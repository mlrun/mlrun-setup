#!/usr/bin/python3
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import urllib.request

import click
import dotenv

logging.basicConfig(level=logging.DEBUG, format="%(levelname)s: %(message)s")
is_dummy_mode = os.environ.get("DUMMY_MODE", "")
default_env_file = "~/.mlrun.env"
scaled_deplyoments = [
    "mlrun-api-chief",
    "mlrun-db",
    "mlrun-jupyter",
    "mlrun-ui",
    "mpi-operator",
    "nuclio-controller",
    "nuclio-dashboard",
]
valid_registry_args = [
    "kind",
    "server",
    "username",
    "password",
    "email",
    "url",
    "secret",
    "push_secret",
]
optional_services = ["spark", "monitoring", "jupyter", "pipelines"]
service_map = {
    "s": "spark-operator",
    "m": "kube-prometheus-stack",
    "j": "jupyterNotebook",
    "p": "pipelines",
}
# auto detect if running inside GitHub Codespaces
is_codespaces = "CODESPACES" in os.environ and "CODESPACE_NAME" in os.environ


class K8sStages:
    none = 0
    namespace = 1
    helm = 2
    registry = 3
    done = 9


# common options
env_file_opt = click.option(
    "--env-file",
    "-f",
    default="",
    help="path to the mlrun .env file (defaults to '~/.mlrun.env')",
)
env_vars_opt = click.option(
    "--env-vars",
    "-e",
    default=[],
    multiple=True,
    help="additional env vars, e.g. -e AWS_ACCESS_KEY_ID=<key-id>",
)
foreground_opt = click.option(
    "--foreground",
    is_flag=True,
    default=False,
    help="run process in the foreground (not as a daemon)",
)


@click.group()
def main():
    """MLRun configuration utility"""
    pass


@main.command(context_settings=dict(ignore_unknown_options=True, allow_extra_args=True))
@env_vars_opt
@env_file_opt
@click.option("--tag", help="MLRun version tag")
@click.option("--force-local", is_flag=True, help="force use of local or docker mlrun")
@click.option("--verbose", "-v", is_flag=True, help="verbose log")
@click.option(
    "--simulate", is_flag=True, help="simulate install (print commands vs exec)"
)
@click.pass_context
def start(
    ctx,
    env_vars,
    env_file,
    tag,
    force_local,
    verbose,
    simulate,
):
    """Start MLRun service, auto detect the best method (local/docker/k8s/remote)"""
    extra_args = {}
    for i in range(0, len(ctx.args), 2):
        if str(ctx.args[i]).startswith("--"):
            key = ctx.args[i][2:].replace("-", "_")
            value = ctx.args[i + 1]
            if key in extra_args.keys():
                current_val = extra_args[key]
                value = (
                    current_val + [value]
                    if isinstance(current_val, list)
                    else [current_val, value]
                )
            extra_args[key] = value

    config = BaseConfig(env_file, verbose, env_vars_opt=env_vars, simulate=simulate)
    current_env_vars = config.get_env()
    last_deployment = current_env_vars.get("MLRUN_CONF_LAST_DEPLOYMENT", "")
    if not force_local and (
        os.environ.get("V3IO_ACCESS_KEY", "") or last_deployment == "remote"
    ):
        dbpath = current_env_vars.get("MLRUN_DBPATH") or os.environ.get(
            "MLRUN_DBPATH", ""
        )
        logging.info(f"detected settings of remote MLRun service at {dbpath}")
        return

    logging.info(extra_args)
    config = K8sConfig.from_config(config)
    if config.is_supported():
        config.start(tag=tag, **extra_args)
        return

    config = DockerConfig.from_config(config)
    if config.is_supported():
        config.start(tag=tag, **extra_args)
        return

    config = LocalConfig.from_config(config)
    config.start(tag=tag, **extra_args)


@main.command()
@env_file_opt
@click.option(
    "--deployment", "-d", help="deployment mode: local | docker | kuberenetes"
)
@click.option(
    "--cleanup",
    "-c",
    is_flag=True,
    help="delete the specified or default env file",
)
@click.option("--force", "-f", is_flag=True, help="force stop")
@click.option("--verbose", "-v", is_flag=True, help="verbose log")
def stop(env_file, deployment, cleanup, force, verbose):
    """Stop MLRun service which was started using this CLI"""
    deployment = deployment or BaseConfig(env_file).get_env().get(
        "MLRUN_CONF_LAST_DEPLOYMENT", ""
    )
    if not deployment:
        logging.error(
            "cannot determine current deployment type, please specify the -d option"
        )
        return
    config = deployment_modes[deployment](env_file, verbose)
    config.stop(force, cleanup)


@main.command()
@env_file_opt
@click.option(
    "--deployment", "-d", help="deployment mode: local | docker | kuberenetes"
)
@click.option("--force", "-f", is_flag=True, help="force stop")
@click.option("--verbose", "-v", is_flag=True, help="verbose log")
def uninstall(env_file, deployment, force, verbose):
    """Uninstall and cleanup MLRun service which was started using this CLI"""
    deployment = deployment or BaseConfig(env_file).get_env().get(
        "MLRUN_CONF_LAST_DEPLOYMENT", ""
    )
    if not deployment:
        logging.error(
            "cannot determine current deployment type, please specify the -d option"
        )
        return
    config = deployment_modes[deployment](env_file, verbose)
    config.stop(force, True)


@main.command()
@env_file_opt
@click.option(
    "--deployment", "-d", help="deployment mode: local | docker | kuberenetes"
)
def pause(env_file, deployment):
    """Scale MLRun deployments to zero
    Plese note -
    if you want to keep your notebooks between deployments scale save your notebooks in the data folder
    """
    deployment = deployment or BaseConfig(env_file).get_env().get(
        "MLRUN_CONF_LAST_DEPLOYMENT", ""
    )
    if not deployment:
        print("cannot determine current deployment type, please specify the -d option")
        return
    config = deployment_modes[deployment](env_file)
    config.pause()


@main.command()
@env_file_opt
@click.option(
    "--services",
    "-s",
    default=[],
    multiple=True,
    help="scale specific services, e.g. -s mlrun-jupyter=0"
    f", supported services: {','.join(scaled_deplyoments)}.",
)
@click.option(
    "--deployment", "-d", help="deployment mode: local | docker | kuberenetes"
)
def scale(env_file, services, deployment):
    """Scale up MLRun deployments"""
    deployment = deployment or BaseConfig(env_file).get_env().get(
        "MLRUN_CONF_LAST_DEPLOYMENT", ""
    )
    if not deployment:
        print("cannot determine current deployment type, please specify the -d option")
        return
    config = deployment_modes[deployment](env_file)
    services = _list2dict(services, default_value="1")
    config.scale(services)


@main.command()
@click.option(
    "--data-volume", "-d", help="host path prefix to the location of db and artifacts"
)
@click.option("--logs-path", "-l", help="logs directory path")
@click.option(
    "--artifact-path", "-a", help="default artifact path (if not in the data volume)"
)
@foreground_opt
@click.option("--port", "-p", help="port to listen on", type=int)
@env_vars_opt
@env_file_opt
@click.option("--tag", help="MLRun version tag")
@click.option(
    "--conda-env",
    help="install and run MLRun API in a the specified conda environment",
    type=str,
)
@click.option("--verbose", "-v", is_flag=True, help="verbose log")
def local(
    data_volume,
    logs_path,
    artifact_path,
    foreground,
    port,
    env_vars,
    env_file,
    tag,
    conda_env,
    verbose,
):
    """Install MLRun service as a local process (limited, no UI and Nuclio)"""
    config = LocalConfig(env_file, verbose, env_vars_opt=env_vars)
    config.start(
        data_volume,
        logs_path,
        artifact_path,
        foreground,
        port,
        tag,
        conda_env,
    )


@main.command()
@click.option(
    "--jupyter",
    "-j",
    is_flag=False,
    flag_value=".",
    default="",
    help="deploy Jupyter container, can provide jupyter image as argument",
)
@click.option(
    "--data-volume", "-d", help="host path prefix to the location of db and artifacts"
)
@click.option(
    "--volume-mount",
    help="container mount path (of the data-volume), when different from host data volume path",
)
@click.option(
    "--artifact-path", "-a", help="default artifact path (if not in the data volume)"
)
@foreground_opt
@click.option("--port", "-p", help="MLRun port to listen on", type=int, default="8080")
@env_vars_opt
@env_file_opt
@click.option("--tag", help="MLRun version tag")
@click.option("--compose-file", help="path to save the generated compose.yaml file")
@click.option("--verbose", "-v", is_flag=True, help="verbose log")
@click.option(
    "--simulate", is_flag=True, help="simulate install (print commands vs exec)"
)
def docker(
    jupyter,
    data_volume,
    volume_mount,
    artifact_path,
    foreground,
    port,
    env_vars,
    env_file,
    tag,
    compose_file,
    verbose,
    simulate,
):
    """Deploy mlrun and nuclio services using Docker compose"""
    config = DockerConfig(env_file, verbose, env_vars_opt=env_vars, simulate=simulate)
    if not config.is_supported(True):
        print("use local or remote service options instead")
        raise SystemExit(1)

    config.start(
        jupyter,
        data_volume,
        volume_mount,
        artifact_path,
        foreground,
        port,
        tag,
        compose_file,
    )


@main.command()
@click.argument("url", type=str, default="", required=True)
@click.option("--username", "-u", help="username (for secure access)")
@click.option("--access-key", "-k", help="access key (for secure access)")
@click.option("--artifact-path", "-p", help="default artifacts path")
@env_file_opt
@env_vars_opt
@click.option("--verbose", "-v", is_flag=True, help="verbose log")
def remote(url, username, access_key, artifact_path, env_file, env_vars, verbose):
    """Connect to remote MLRun service (over Kubernetes)"""
    config = RemoteConfig(env_file, verbose, env_vars_opt=env_vars)
    config.start(url, username, access_key, artifact_path)


@main.command()
@click.option("--name", "-n", default="mlrun-ce", help="helm deployment name")
@click.option("--namespace", default="mlrun", help="kubernetes namespace")
@click.option(
    "--registry-args",
    "-r",
    default=[],
    multiple=True,
    help="docker registry args, can be a kind string (local, docker, ..) or a set of key=value args e.g. "
    f"-r username=joe -r password=j123 -r email=joe@email.com, supported keys: {','.join(valid_registry_args)}",
)
@click.option(
    "--options",
    "-o",
    default=[],
    multiple=True,
    help=f"optional services to enable, supported services: {','.join(optional_services)}",
)
@click.option(
    "--disable",
    "-d",
    default=[],
    multiple=True,
    help=f"optional services to disable, supported services: {','.join(optional_services)}",
)
@click.option(
    "--set",
    "-s",
    "settings",
    default=[],
    multiple=True,
    help="Additional helm --set commands, accept multiple --set options",
)
@click.option("--external-addr", help="external ip/dns address", type=str)
@click.option("--tag", help="MLRun version tag")
@env_file_opt
@env_vars_opt
@click.option("--verbose", "-v", is_flag=True, help="verbose log")
@click.option(
    "--simulate", is_flag=True, help="simulate install (print commands vs exec)"
)
@click.option("--chart-ver", help="MLRun helm chart version")
def kubernetes(
    name,
    namespace,
    registry_args,
    options,
    disable,
    settings,
    external_addr,
    tag,
    env_file,
    env_vars,
    verbose,
    simulate,
    chart_ver,
):
    """Install MLRun service on Kubernetes"""
    config = K8sConfig(env_file, verbose, env_vars_opt=env_vars, simulate=simulate)
    if not config.is_supported(True):
        print("you can try other mlrun deployment options (local, docker)")
        raise SystemExit(1)
    config.start(
        name,
        namespace,
        registry_args,
        external_addr,
        tag,
        settings,
        options,
        disable,
        chart_ver,
    )


@main.command()
@click.option("--api", "-a", type=str, help="api service url")
@click.option("--username", "-u", help="username (for secure access)")
@click.option("--access-key", "-k", help="access key (for secure access)")
@click.option("--artifact-path", "-p", help="default artifacts path")
@env_file_opt
@env_vars_opt
def set(api, username, access_key, artifact_path, env_file, env_vars):
    """Set configuration in mlrun default or specified .env file"""
    config = BaseConfig(env_file, env_vars_opt=env_vars)
    if not os.path.isfile(config.filename):
        print(
            f".env file {config.filename} not found, creating new and setting configuration"
        )
    else:
        print(f"updating configuration in .env file {config.filename}")
    env_dict = {
        "MLRUN_DBPATH": api,
        "MLRUN_ARTIFACT_PATH": artifact_path,
        "V3IO_USERNAME": username,
        "V3IO_ACCESS_KEY": access_key,
    }
    config.set_mlrun_env(env_dict)


@main.command()
@env_file_opt
def get(env_file):
    """Print the local or remote configuration"""
    config = BaseConfig(env_file)
    if not os.path.isfile(config.filename):
        print(f"error, env file {env_file} does not exist")
        exit(1)

    print(f"Env file {config.filename} content:")
    with open(config.filename, "r") as fp:
        print(fp.read())


@main.command()
@env_file_opt
def clear(env_file):
    """Delete the default or specified config .env file"""
    BaseConfig(env_file).clear_env(True)


@main.command()
def latest():
    """Get the latest MLRun version"""
    get_latest_mlrun_tag()


class BaseConfig:
    def __init__(self, env_file, verbose=False, env_vars_opt=None, simulate=False):
        self.env_file = env_file
        self.filename = os.path.expanduser(env_file or default_env_file)
        self.verbose = verbose
        self.env_vars_opt = env_vars_opt
        self.simulate = simulate or is_dummy_mode
        self._env_dict = None

    @classmethod
    def from_config(cls, other_config):
        config = cls(
            other_config.env_file,
            other_config.verbose,
            other_config.env_vars_opt,
            other_config.simulate,
        )
        config._env_dict = other_config._env_dict
        return config

    def get_env(self, refresh=False):
        if not self._env_dict or refresh:
            self._env_dict = dotenv.dotenv_values(self.filename)
        return self._env_dict

    def set_env(self, env_vars):
        for key, value in env_vars.items():
            if value is not None:
                dotenv.set_key(self.filename, key, str(value), quote_mode="")
        if self.env_vars_opt:
            for key, value in _list2dict(self.env_vars_opt).items():
                dotenv.set_key(self.filename, key, value, quote_mode="")
        if self.env_file:
            env_file = self.env_file
            # if its not the default file print the usage details
            print(
                f"to use the {env_file} .env file add MLRUN_ENV_FILE={env_file} to your development environment\n"
                f"or call `mlrun.set_env_from_file({env_file}) in the beginning of your code"
            )

    def clear_env(self, delete_file=None, delete_keys=None):
        if os.path.isfile(self.filename):
            if delete_file:
                print(f"deleting env file {self.filename}")
                os.remove(self.filename)
            else:
                for key in [
                    "MLRUN_DBPATH",
                    "MLRUN_CONF_LAST_DEPLOYMENT",
                    "MLRUN_MOCK_NUCLIO_DEPLOYMENT",
                ] + (delete_keys or []):
                    dotenv.unset_key(self.filename, key)
        else:
            print(f".env file {self.filename} not found")

    def do_popen(self, cmd, env=None, interactive=True, stdin=None):
        if self.simulate:
            print(f"DUMMY: {' '.join(cmd)}")
            return 0, "", ""

        output = None if interactive else subprocess.PIPE
        stdin = None if not stdin else stdin
        if self.verbose:
            print(cmd)
        try:
            child = subprocess.Popen(
                cmd, env=env, stdin=stdin, stdout=output, stderr=output
            )
        except FileNotFoundError as exc:
            if interactive or self.verbose:
                print(str(exc))
            return 99, "", ""

        returncode = child.wait()
        if interactive:
            return returncode, "", ""

        return (
            returncode,
            child.stdout.read().decode("utf-8"),
            child.stderr.read().decode("utf-8"),
        )

    def start(self, **kwargs):
        pass

    def stop(self, force=None, cleanup=None):
        pass

    def pause(self, **kwargs):
        pass

    def scale(self, services: dict = None):
        pass

    def is_supported(self, print_error=False):
        return True


class RemoteConfig(BaseConfig):
    def start(self, url, username, access_key, artifact_path, env_file, env_vars):
        config = {"MLRUN_DBPATH": url, "MLRUN_CONF_LAST_DEPLOYMENT": "remote"}
        if artifact_path:
            config["V3IO_USERNAME"] = username
        if artifact_path:
            config["V3IO_ACCESS_KEY"] = access_key
        if artifact_path:
            config["MLRUN_ARTIFACT_PATH"] = artifact_path
        self.set_env(config, env_vars_opt=env_vars)


class LocalConfig(BaseConfig):
    @staticmethod
    def find_python_exec():
        if "python" in sys.executable:
            return sys.executable

        python_exec = os.environ.get("PYTHON_EXEC")
        if python_exec and shutil.which(python_exec):
            return python_exec

        for executable in ["python3", "python"]:
            if shutil.which(executable):
                return executable

        print(
            "Error, python executable was not found or is not accessible, please install it first\n"
            "if its installed, specify the python executable path using the PYTHON_EXEC env variable "
        )
        exit(1)

    def start(
        self,
        data_volume=None,
        logs_path=None,
        artifact_path=None,
        foreground=None,
        port=None,
        tag=None,
        conda_env=None,
        **kwargs,
    ):
        env = {"MLRUN_IGNORE_ENV_FILE": "true"}
        data_volume = data_volume or os.environ.get("SHARED_DIR", "")
        artifact_path = artifact_path or os.environ.get("MLRUN_ARTIFACT_PATH", "")
        tag = tag or get_latest_mlrun_tag()

        if not port and "COLAB_RELEASE_TAG" in os.environ:
            # change default port due to conflict in google colab
            port = 8089

        self.install_mlrun_api(tag, conda_env)

        cmd = [self.find_python_exec(), "-m", "mlrun", "db"]
        cmd += ["--update-env", self.filename]
        if not foreground:
            cmd += ["-b"]
        if port is not None:
            cmd += ["-p", str(port)]
        if data_volume is not None:
            cmd += ["-v", data_volume]
            env["MLRUN_HTTPDB__LOGS_PATH"] = data_volume.rstrip("/") + "/logs"
        if logs_path is not None:
            env["MLRUN_HTTPDB__LOGS_PATH"] = logs_path
        if self.verbose:
            cmd += ["--verbose"]
        if artifact_path:
            cmd += ["-a", artifact_path]

        if conda_env:
            cmd = ["conda" "run" "-n", conda_env, "python"] + cmd[1:]
        returncode, _, _ = self.do_popen(cmd, env=env)
        if returncode != 0:
            raise SystemExit(returncode)

        # todo: wait to see the db is up

        self.set_env(
            {
                "MLRUN_DBPATH": f"http://localhost:{port or '8080'}",
                "MLRUN_MOCK_NUCLIO_DEPLOYMENT": "auto",
                "MLRUN_CONF_LAST_DEPLOYMENT": "local",
                "MLRUN_STORAGE__ITEM_TO_REAL_PATH": "",
            },
        )

    def stop(self, force=None, cleanup=None):
        pid = int(self.get_env().get("MLRUN_CONF_SERVICE_PID", "0"))
        if pid and self.pid_exists(pid):
            os.kill(pid)
        self.clear_env(cleanup)

    def install_mlrun_api(self, tag, conda_env=None):
        mlrun_env = self.get_env()
        installed = mlrun_env.get("MLRUN_CONF_API_IS_INSTALLED")
        prefix = [self.find_python_exec()]
        if conda_env:
            prefix = ["conda" "run" "-n", conda_env, "python"]

        if not installed:
            # check if mlrun + api packages are installed
            returncode, _, _ = self.do_popen(
                prefix + ["-c", "import mlrun, apscheduler, uvicorn"], interactive=False
            )
            installed = returncode == 0

        if not installed:
            package = "mlrun[api]"
            if tag:
                package += f"=={tag}"
            cmd = ["-m", "pip", "install", package]
            returncode, _, err = self.do_popen(prefix + cmd, interactive=False)
            if returncode != 0:
                print(err)

    @staticmethod
    def pid_exists(pid):
        """Check whether pid exists in the current process table."""
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True


class DockerConfig(BaseConfig):
    def is_supported(self, print_error=False):
        for executable in ["docker", "docker-compose"]:
            if shutil.which(executable) is None:
                if print_error or self.verbose:
                    print(
                        f"Error, {executable} executable was not found"
                        ", please make sure it is installed and accessible"
                    )
                return False
        docker_runs = self.do_popen(["docker", "ps"])[0] == 0
        if not docker_runs and (print_error or self.verbose):
            print("docker process failed to execute")
        if self.verbose and docker_runs:
            print("docker support was detected")
        return docker_runs

    def start(
        self,
        jupyter,
        data_volume,
        volume_mount,
        artifact_path,
        foreground,
        port,
        tag,
        compose_file,
        **kwargs,
    ):
        """Deploy mlrun and nuclio services using Docker compose"""

        if is_codespaces:
            volume_mount = volume_mount or "/tmp/mlrun"
            data_volume = data_volume or "/mnt/containerTmp/mlrun"
            os.makedirs(volume_mount, exist_ok=True)
        data_volume = os.path.realpath(
            os.path.expanduser(data_volume or "~/mlrun-data")
        )
        volume_mount = volume_mount or data_volume
        docker_volume_mount = _docker_path(volume_mount)
        if not is_codespaces:
            try:
                os.makedirs(data_volume, exist_ok=True)
            except PermissionError as exc:
                # can be caused if the script runs on another container
                print(f"Error making volume {data_volume}, {exc}")

        tag = tag or get_latest_mlrun_tag()
        compose_file = compose_file or "compose.yaml"
        cmd = ["docker-compose", "-f", compose_file, "up"]
        if not foreground:
            cmd += ["-d"]

        compose_body = compose_template + mlrun_api_template
        jupyter_image = ""
        if jupyter:
            compose_body += jupyter_template
            jupyter_image = f"mlrun/jupyter:{tag}" if jupyter == "." else jupyter
            logging.info(f"Jupyter container image: {jupyter_image} ")
        compose_body += suffix_template
        with open(compose_file, "w") as fp:
            fp.write(compose_body)

        env = os.environ.copy()
        for key, val in {
            "HOST_IP": _get_ip(),
            "SHARED_DIR": _docker_path(data_volume),  # host dir
            "VOLUME_MOUNT": volume_mount,  # mounted dir
            "MLRUN_PORT": str(port),
            "TAG": tag,
            "JUPYTER_IMAGE": jupyter_image,
        }.items():
            print(f"{key}={val}")
            if val is not None:
                env[key] = val

        path_map = None
        if volume_mount != docker_volume_mount:
            path_map == f"{docker_volume_mount}::{volume_mount}"
        self.set_env(
            {
                "MLRUN_DBPATH": f"http://localhost:{port}",
                "MLRUN_MOCK_NUCLIO_DEPLOYMENT": "",
                "MLRUN_CONF_LAST_DEPLOYMENT": "docker",
                "MLRUN_CONF_COMPOSE_PATH": os.path.realpath(compose_file),
                "MLRUN_STORAGE__ITEM_TO_REAL_PATH": path_map,
            },
        )

        print(cmd)
        returncode, _, _ = self.do_popen(cmd, env=env)
        if returncode != 0:
            raise SystemExit(returncode)

        print()
        print(f"MLRun API address: http://localhost:{port}")
        print(
            f"MLRun UI address:  http://localhost:{os.environ.get('MLRUN_UI_PORT', '8060')}"
        )
        print(
            f"Nuclio UI address: http://localhost:{os.environ.get('NUCLIO_PORT', '8070')}"
        )
        if jupyter:
            print("Jupyter address:   http://localhost:8888")

    def stop(self, force=None, cleanup=None):
        compose_file = self.get_env().get("MLRUN_CONF_COMPOSE_PATH", "")
        if compose_file:
            returncode, _, _ = self.do_popen(
                ["docker-compose", "-f", compose_file, "down"]
            )
            if returncode != 0:
                self.set_env({"MLRUN_DBPATH": ""})  # disable the DB access
                raise SystemExit(returncode)
        self.stop_nuclio_containers()
        self.clear_env(cleanup)

    def stop_nuclio_containers(self):
        cmd = [
            "docker",
            "ps",
            "--format",
            "{{.ID}}",
            "-f",
            "label=nuclio.io/function-name",
        ]
        returncode, out, err = self.do_popen(cmd, interactive=False)
        if returncode != 0:
            print(err)
            return
        containers = out.split()
        if not containers:
            return
        print(f"Stopping nuclio function containers: {' '.join(containers)}")
        cmd = ["docker", "stop"] + containers
        returncode, _, err = self.do_popen(cmd, interactive=False)
        if returncode != 0:
            print(err)


class K8sConfig(BaseConfig):
    def is_supported(self, print_error=False):
        if shutil.which("kubectl") is None:
            if print_error or self.verbose:
                logging.error(
                    "Error, kubectl was not found, "
                    "please make sure Kubernetes is installed and configured"
                )
            return False
        if shutil.which("helm") is None:
            if print_error or self.verbose:
                logging.error(
                    "Error, helm was not found, please make sure helm is installed"
                    " see: https://helm.sh/docs/intro/install/"
                )
            return False
        kubectl_runs = self.do_popen(["kubectl", "version"])[0] == 0
        if not kubectl_runs and (print_error or self.verbose):
            logging.error("Kubernetes cli (kubectl) is not accessible")
        if self.verbose and kubectl_runs:
            logging.error("Kubernetes (kubectl) support was detected")
        return kubectl_runs

    def start(
        self,
        name="mlrun-ce",
        namespace="mlrun",
        registry_args=None,
        external_addr=None,
        tag=None,
        settings=None,
        options=None,
        disable=None,
        chart_ver=None,
        **kwargs,
    ):
        logging.info("Start installing MLRun CE")
        service_options = self.parse_services(options, enable="true")
        service_disable = self.parse_services(disable, enable="false")
        tag = tag or get_latest_mlrun_tag()
        logging.info(f"Using MLRun tag: {tag} ")
        logging.info(f"Creating kubernetes namespace {namespace}...")
        create_namespace = True
        if self.check_k8s_resource_exist("namespace", namespace):
            logging.warning(f"Namespace {namespace} already exists")
            text = click.prompt(
                "To overwrite the existing namespace press y or Y",
                type=str,
                default="n",
            )
            text = text.lower()
            if "y" in text:
                returncode, out, err = self.do_popen(
                    ["kubectl", "delete", "namespace", namespace], interactive=False
                )
                if returncode != 0:
                    logging.error(err)
                    raise SystemExit(returncode)
            else:
                create_namespace = False
        if create_namespace:
            returncode, out, err = self.do_popen(
                ["kubectl", "create", "namespace", namespace], interactive=True
            )
            if returncode != 0:
                # err = child.stderr.read().decode("utf-8")
                if "AlreadyExists" not in err:
                    logging.error(err)
                    raise SystemExit(returncode)
        env_settings = {
            "MLRUN_MOCK_NUCLIO_DEPLOYMENT": "",
            "MLRUN_CONF_LAST_DEPLOYMENT": "kubernetes",
            "MLRUN_CONF_HELM_DEPLOYMENT": name,
            "MLRUN_CONF_K8S_NAMESPACE": namespace,
            "MLRUN_CONF_K8S_STAGE": K8sStages.namespace,
        }
        self.set_env(env_settings)

        # Install and update Helm charts
        helm_commands = [
            ["helm", "repo", "add", "mlrun-ce", "https://mlrun.github.io/ce"],
            ["helm", "repo", "list"],
            ["helm", "repo", "update"],
        ]

        logging.info("Installing and updating mlrun helm repo")
        for command in helm_commands:
            returncode, _, _ = self.do_popen(command)
            if returncode != 0:
                raise SystemExit(returncode)
        env_settings["MLRUN_CONF_K8S_STAGE"] = K8sStages.helm
        self.set_env(env_settings)

        # create or get docker registry settings
        registry_url, pull_secret, push_secret, new_settings = self.configure_registry(
            namespace, registry_args
        )
        env_settings["MLRUN_CONF_K8S_STAGE"] = K8sStages.registry
        for setting, value in new_settings.items():
            env_settings["MLRUN_CONF_K8S_" + setting] = value
        self.set_env(env_settings)

        # run helm to install mlrun
        helm_run_cmd = [
            "helm",
            "--namespace",
            namespace,
            "install",
            name,
            "--wait",
            "--timeout",
            "960s",
            "--set",
            f"global.registry.url={registry_url}",
        ]
        if pull_secret:
            helm_run_cmd += ["--set", f"global.registry.secretName={pull_secret}"]
        if push_secret:
            helm_run_cmd += [
                "--set",
                f"nuclio.dashboard.kaniko.registryProviderSecretName={push_secret}",
                "--set",
                f"mlrun.defaultDockerRegistrySecretName={push_secret}",
            ]
        if external_addr:
            helm_run_cmd += ["--set", f"global.externalHostAddress={external_addr}"]
        # todo: in case of jupyter image set the jupyterNotebook.image.repository & tag
        if tag:
            for service in ["mlrun.api", "mlrun.ui", "jupyterNotebook"]:
                helm_run_cmd += ["--set", f"{service}.image.tag={tag}"]
        if settings:
            for setting in settings:
                helm_run_cmd += ["--set", setting]
        for opt in service_options:
            helm_run_cmd += ["--set", opt]
        for opt in service_disable:
            helm_run_cmd += ["--set", opt]
        if chart_ver:
            helm_run_cmd += ["--version", chart_ver]

        if self.verbose:
            helm_run_cmd += ["--debug"]
        helm_run_cmd += ["mlrun-ce/mlrun-ce"]

        logging.info("Running helm install...")
        returncode, _, _ = self.do_popen(helm_run_cmd)
        if returncode != 0:
            raise SystemExit(returncode)

        dbpath = f"http://{external_addr or 'localhost'}:{30070}"
        env_settings["MLRUN_CONF_K8S_STAGE"] = K8sStages.done
        env_settings["MLRUN_DBPATH"] = dbpath
        print(
            "configure your mlrun client environment to use the installed service:\n"
            f"mlrun config set -a {dbpath}"
        )
        self.set_env(env_settings)

    @staticmethod
    def parse_services(include, enable):
        extra_sets = []
        if include:
            for service in include:
                if (
                    service not in optional_services
                    and service not in service_map.keys()
                ):
                    raise ValueError(
                        f"illegal service name {service}, "
                        f"optional services are {','.join(optional_services)}"
                    )
                extra_sets.append(service_map[service[0]] + f".enabled={enable}")
        return extra_sets

    def configure_registry(self, namespace, registry_args):
        # del registry secret before create
        # returns url, secret, push_secret, new_settings
        if not registry_args and "DOCKER_USERNAME" not in os.environ:
            logging.info(
                "please select the container registry kind, Local, docker "
                "(for docker hub), or args e.g. url=<registry-url>"
            )
            text = click.prompt(
                "container registry kind or args", type=str, default="local"
            )
            registry_args = text.split(",")

        if not isinstance(registry_args, dict):
            registry_args = _list2dict(registry_args, "kind")
        for key in registry_args.keys():
            if key not in valid_registry_args:
                raise ValueError(
                    f"illegal docker registry arg {key}, valid args: {','.join(valid_registry_args)}"
                )

        kind = registry_args.get("kind", "docker")
        registry_service = registry_args.get("service", "")
        url = registry_args.get("url", "")
        pull_secret = registry_args.get("secret", "")
        push_secret = registry_args.get("push_secret", "")
        new_settings = {}

        if kind == "local":
            # use local docker registry, create it if needed
            if not url:
                logging.info("Starting local docker registry...")
                cmd = "docker run -d -p 5000:5000 --name docker-registry registry:2.7".split()
                returncode, _, _ = self.do_popen(cmd)
                if returncode != 0:
                    raise SystemExit(returncode)
                new_settings["DOCKER_REGISTRY"] = "docker-registry"
                url = f"{_get_ip()}:5000"

            return url, "", push_secret, new_settings

        if pull_secret:
            # secret specified by user, skip creation
            if not url:
                raise ValueError(
                    "docker registry url must be specified along with the secret name"
                )
            return url, pull_secret, push_secret, new_settings

        # create secret for pull registry
        registry_username = (
            registry_args.get("username")
            or os.environ.get("DOCKER_USERNAME")
            or click.prompt("Docker registry username", type=str)
        )
        registry_password = (
            registry_args.get("password")
            or os.environ.get("DOCKER_PASSWORD")
            or click.prompt("Docker registry password", type=str, hide_input=True)
        )
        registry_email = registry_args.get("email")
        if kind in ["docker"]:
            # email is not mandatory in all registries
            registry_email = (
                registry_email
                or os.environ.get("DOCKER_EMAIL")
                or click.prompt("Docker registry email", type=str)
            )

        # todo: default and url based on kind (docker, ecr, gcr, ..)
        registry_service = registry_service or "https://index.docker.io/v1/"
        url = url or f"index.docker.io/{registry_username}"

        pull_secret = "registry-credentials"
        docker_secret_cmd = [
            "kubectl",
            "--namespace",
            namespace,
            "create",
            "secret",
            "docker-registry",
            pull_secret,
            "--docker-server",
            registry_service,
            "--docker-username",
            registry_username,
            "--docker-password",
            registry_password,
            "--docker-email",
            registry_email,
        ]
        new_settings["REGISTRY_SECRET"] = pull_secret
        create_secret = True
        if self.check_k8s_resource_exist(
            namespace=namespace, resource="secret", name=pull_secret
        ):
            logging.warning(f"Registry Secret {pull_secret} already exists")
            text = click.prompt(
                "To overwrite the existing Registry Secret press y or Y",
                type=str,
                default="n",
            )
            text = text.lower()
            if "y" in text:
                returncode, _, _ = self.do_popen(
                    ["kubectl", "-n", namespace, "delete", "secret", pull_secret]
                )
                if returncode != 0:
                    raise SystemExit(returncode)
            else:
                create_secret = False
        if create_secret:
            returncode, _, _ = self.do_popen(docker_secret_cmd)
            if returncode != 0:
                logging.error("Failed to create secret !")
                raise SystemExit(returncode)

        return url, pull_secret, push_secret, new_settings

    def pause(self):
        env = self.get_env()
        namespace = env.get("MLRUN_CONF_K8S_NAMESPACE", "")
        cmd = ["kubectl", "-n", namespace, "scale", "deployments.apps"]
        for deployment in scaled_deplyoments:
            cmd.append(deployment)
        cmd.append("--replicas=0")
        returncode, _, _ = self.do_popen(cmd)
        if returncode != 0:
            raise SystemExit(returncode)
        self.check_scale(method="pause", namespace=namespace)
        logging.info(f"All Deployments are Scaled to zero")

    def scale(self, services: dict = None):
        print(1)
        env = self.get_env()
        namespace = env.get("MLRUN_CONF_K8S_NAMESPACE", "")
        cmd = ["kubectl", "-n", namespace, "scale", "deployments.apps"]
        deployments = services.keys() if services else scaled_deplyoments
        for deployment in deployments:
            cmd.append(deployment)
            replica_num = services.get(deployment, "1")
            cmd.append(f"--replicas={replica_num}")
            returncode, _, _ = self.do_popen(cmd)
            if returncode != 0:
                raise SystemExit(returncode)
            cmd = cmd[:5]
        self.check_scale(method="scale", namespace=namespace)
        logging.info(f'scaled {",".join(scaled_deplyoments)}')

    def stop(self, force=None, cleanup=None):
        env = self.get_env()
        stage = int(env.get("MLRUN_CONF_K8S_STAGE", "0"))
        if not stage:
            logging.error("mlrun kubernetes installation was not detected")
            return

        delete_keys = []  # add additional keys to delete per uninstall section
        helm_name = env.get("MLRUN_CONF_HELM_DEPLOYMENT", "")
        namespace = env.get("MLRUN_CONF_K8S_NAMESPACE", "")
        if helm_name and stage >= K8sStages.done:
            # uninstall the helm chart
            cmd = ["helm", "--namespace", namespace, "uninstall", helm_name, "--wait"]
            if self.verbose:
                cmd += ["--debug"]
            returncode, _, _ = self.do_popen(cmd)
            if returncode != 0:
                self.set_env({"MLRUN_DBPATH": ""})  # disable the DB access
                raise SystemExit(returncode)

            self.set_env({"MLRUN_CONF_K8S_STAGE": str(K8sStages.registry)})

        if cleanup and stage >= K8sStages.registry:
            # remove the registry service and/or secrets
            pull_secret = env.get("MLRUN_CONF_K8S_REGISTRY_SECRET", "")
            registry = env.get("MLRUN_CONF_K8S_DOCKER_REGISTRY", "")
            if pull_secret:
                returncode, _, _ = self.do_popen(
                    [
                        "kubectl",
                        "--namespace",
                        namespace,
                        "delete",
                        "secret",
                        pull_secret,
                    ]
                )
            if registry:
                self.do_popen(["docker", "rm", "-f", registry])

        if cleanup and stage >= K8sStages.namespace:
            # delete pods and the namespace
            self.do_popen(
                [
                    "kubectl",
                    "--namespace",
                    namespace,
                    "delete",
                    "pod",
                    "--force",
                    "--grace-period=0",
                    "--all",
                ]
            )
            self.do_popen(["kubectl", "delete", "namespace", namespace])

        self.clear_env(cleanup, delete_keys=delete_keys)

    def check_scale(self, method, namespace, services=None):
        i_scale = "1" if method == "scale" else "0"
        # todo: support check scale for more then one replica
        def check_scale_status(i_scale, namespace):
            cmd = ["kubectl", "-n", namespace, "get", "deployments.apps"]
            if self.simulate:
                print(f"DUMMY: {' '.join(cmd)}")
                return 0, "", ""
            if self.verbose:
                print(cmd)
            child = subprocess.Popen(
                cmd,
                env=None,
                stdin=None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            cmd = ["awk", "{print $4}"]
            child = subprocess.Popen(
                cmd,
                env=None,
                stdin=child.stdout,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            cmd = ["tail", "-n", "+2"]
            child = subprocess.Popen(
                cmd,
                env=None,
                stdin=child.stdout,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            cmd = ["grep", i_scale]
            child = subprocess.Popen(
                cmd,
                env=None,
                stdin=child.stdout,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            cmd = ["wc", "-l"]
            child = subprocess.Popen(
                cmd,
                env=None,
                stdin=child.stdout,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            return int(child.stdout.read().decode("utf-8"))

        stop = check_scale_status(i_scale, namespace)
        while stop < len(scaled_deplyoments):
            stop = check_scale_status(i_scale)

    def check_k8s_resource_exist(self, resource: str, name: str, namespace: str = None):
        cmd = ["kubectl", "get", resource, name]
        if namespace:
            cmd = ["kubectl", "-n", namespace, "get", resource, name]
        returncode, out, err = self.do_popen(cmd, interactive=False)
        if returncode == 1:
            return False
        else:
            return True


def _get_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(0)
    try:
        # doesn't even have to be reachable
        s.connect(("10.254.254.254", 1))
        IP = s.getsockname()[0]
    except Exception:
        IP = "127.0.0.1"
    finally:
        s.close()
    return IP


def _list2dict(lines: list, default_key="", default_value=None):
    out = {}
    for line in lines:
        i = line.find("=")
        if i == -1:
            line = line.strip()
            if line and default_key:
                out[default_key] = line
            elif line and default_value is not None:
                out[line] = default_value
            continue
        key, value = line[:i].strip(), line[i + 1 :].strip()
        if key is None:
            raise ValueError("cannot find key in line (key=value)")
        value = os.path.expandvars(value)
        out[key] = value
    return out


def _docker_path(filepath: str):
    if re.match(r"^[a-zA-Z]:\\.?", filepath):
        # convert windows paths to docker style
        filepath = "/" + filepath[0].lower() + filepath[2:].replace("\\", "/")
    return filepath


def get_latest_mlrun_tag():
    mlrun_releases = "https://api.github.com/repos/mlrun/mlrun/releases/latest"
    try:
        with urllib.request.urlopen(mlrun_releases) as response:
            data = response.read()
            data = json.loads(data)
            tag = data["tag_name"][1:]
            print(f"latest MLRun version detected: {tag}")
            return tag
    except Exception as exc:
        print(f"cant read mlrun releases from {mlrun_releases}, {exc}")
    return ""


compose_template = """
services:
  init_nuclio:
    image: alpine:3.16
    command:
      - "/bin/sh"
      - "-c"
      - |
        mkdir -p /etc/nuclio/config/platform; \
        cat << EOF | tee /etc/nuclio/config/platform/platform.yaml
        runtime:
          common:
            env:
              MLRUN_DBPATH: http://mlrun-api:8080
              MLRUN_STORAGE__ITEM_TO_REAL_PATH: c:\\::/c/
        local:
          defaultFunctionContainerNetworkName: mlrun
          defaultFunctionRestartPolicy:
            name: always
            maxRetryCount: 0
          defaultFunctionVolumes:
            - volume:
                name: mlrun-stuff
                hostPath:
                  path: ${SHARED_DIR}
              volumeMount:
                name: mlrun-stuff
                mountPath: ${VOLUME_MOUNT}
        logger:
          sinks:
            myStdoutLoggerSink:
              kind: stdout
          system:
            - level: debug
              sink: myStdoutLoggerSink
          functions:
            - level: debug
              sink: myStdoutLoggerSink
        EOF
    volumes:
      - nuclio-platform-config:/etc/nuclio/config

  mlrun-ui:
    image: "mlrun/mlrun-ui:${TAG:-1.2.1}"
    ports:
      - "8060:8090"
    environment:
      MLRUN_API_PROXY_URL: http://mlrun-api:8080
      MLRUN_NUCLIO_MODE: enable
      MLRUN_NUCLIO_API_URL: http://nuclio:8070
      MLRUN_NUCLIO_UI_URL: http://localhost:${NUCLIO_PORT:-8070}
    networks:
      - mlrun

  nuclio:
    image: "quay.io/nuclio/dashboard:${NUCLIO_TAG:-stable-amd64}"
    ports:
      - "8070:8070"
    environment:
      NUCLIO_DASHBOARD_EXTERNAL_IP_ADDRESSES: "${HOST_IP}"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - nuclio-platform-config:/etc/nuclio/config
    depends_on:
      - init_nuclio
    networks:
      - mlrun
"""

mlrun_api_template = """
  mlrun-api:
    image: "mlrun/mlrun-api:${TAG:-1.2.1}"
    ports:
      - "${MLRUN_PORT:-8080}:8080"
    environment:
      MLRUN_ARTIFACT_PATH: "${VOLUME_MOUNT}/{{project}}"
      # using local storage, meaning files / artifacts are stored locally, so we want to allow access to them
      MLRUN_HTTPDB__REAL_PATH: /data
      MLRUN_HTTPDB__DATA_VOLUME: "${VOLUME_MOUNT}"
      MLRUN_LOG_LEVEL: DEBUG
      MLRUN_NUCLIO_DASHBOARD_URL: http://nuclio:${NUCLIO_PORT:-8070}
      MLRUN_HTTPDB__DSN: "sqlite:////data/mlrun.db?check_same_thread=false"
      MLRUN_UI__URL: http://localhost:${MLRUN_UI_PORT:-8060}
      # not running on k8s meaning no need to store secrets
      MLRUN_SECRET_STORES__KUBERNETES__AUTO_ADD_PROJECT_SECRETS: "false"
      # let mlrun control nuclio resources
      MLRUN_HTTPDB__PROJECTS__FOLLOWERS: "nuclio"
    volumes:
      - "${SHARED_DIR}:/data"
    networks:
      - mlrun
"""

jupyter_template = """
  jupyter:
    image: "{JUPYTER_IMAGE}"
    command:
      - start-notebook.sh
      - "--ip='0.0.0.0'"
      - --port=8888
      - "--NotebookApp.token=''"
      - "--NotebookApp.password=''"
      - "--NotebookApp.default_url='/lab'"
    ports:
      - "8888:8888"
    environment:
      MLRUN_DBPATH: http://mlrun-api:${MLRUN_PORT:-8080}
    volumes:
      - "${SHARED_DIR}:${VOLUME_MOUNT}"
    networks:
      - mlrun
"""

suffix_template = """
volumes:
  nuclio-platform-config: {}

networks:
  mlrun:
    name: mlrun
"""


deployment_modes = {
    "local": LocalConfig,
    "docker": DockerConfig,
    "kubernetes": K8sConfig,
    "remote": RemoteConfig,
}


if __name__ == "__main__":
    main()
