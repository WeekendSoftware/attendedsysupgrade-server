import threading
import glob
import re
from queue import Queue
import shutil
import tempfile
import os
import os.path
import subprocess
import logging
import time
import pprint

from utils.image import Image
from utils.common import get_hash
from utils.config import Config
from utils.database import Database

class GarbageCollector(threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self)
        self.log = logging.getLogger(__name__)
        self.config = Config()
        self.database = Database(self.config)

    def del_image(self, image):
        image_hash, image_path = image
        self.log.debug("remove outdated image %s", image)
        self.database.del_image(image_hash)
        if os.path.exists(image_path):
            shutil.rmtree(image_path)

    def run(self):
        while True:
            # remove outdated snapshot builds
            for outdated_snapshot in self.database.get_outdated_snapshots():
                self.del_image(outdated_snapshot)

            # del custom images older than 7 days
            for outdated_custom in self.database.get_outdated_customs():
                self.del_image(outdated_custom)

            # del oudated manifests
            for outdated_manifest in self.database.get_outdated_manifests():
                self.del_image(outdated_manifest)

            # del outdated snapshot requests
            self.database.del_outdated_request()

            # run every 6 hours
            time.sleep(3600 * 6)

class Worker(threading.Thread):
    def __init__(self, location, job, queue):
        self.location = location
        self.queue = queue
        self.job = job
        threading.Thread.__init__(self)
        self.log = logging.getLogger(__name__)
        self.log.info("log initialized")
        self.config = Config()
        self.log.info("config initialized")
        self.database = Database(self.config)
        self.log.info("database initialized")

    def setup_meta(self):
        self.log.debug("setup meta")
        os.makedirs(self.location, exist_ok=True)
        if not os.path.exists(self.location + "/meta"):
            cmdline = "git clone https://github.com/aparcar/meta-imagebuilder.git ."
            proc = subprocess.Popen(
                cmdline.split(" "),
                cwd=self.location,
                stdout=subprocess.PIPE,
                shell=False,
            )

            _, errors = proc.communicate()
            return_code = proc.returncode

            if return_code != 0:
                self.log.error("failed to setup meta ImageBuilder")
                exit()

        self.log.info("meta ImageBuilder successfully setup")

    def write_log(self, path, stdout=None, stderr=None):
        with open(path, "a") as log_file:
            log_file.write("### BUILD COMMAND:\n\n")
            for key, value in self.params.items():
                log_file.write("{}={}\n".format(key.upper(), str(value)))
            log_file.write("sh meta\n")
            if stdout:
                log_file.write("\n\n### STDOUT:\n\n" + stdout)
            if stderr:
                log_file.write("\n\n### STDERR:\n\n" + stderr)

    # build image
    def build(self):
        self.log.debug("create and parse manifest")

        # fail path in case of erros
        fail_log_path = self.config.get_folder("download_folder") + "/faillogs/faillog-{}.txt".format(self.params["request_hash"])

        self.image = Image(self.params)

        # first determine the resulting manifest hash
        return_code, manifest_content, errors = self.run_meta("manifest")

        if return_code == 0:
            self.image.params["manifest_hash"] = get_hash(manifest_content, 15)

            manifest_pattern = r"(.+) - (.+)\n"
            manifest_packages = dict(re.findall(manifest_pattern, manifest_content))
            self.database.add_manifest_packages(self.image.params["manifest_hash"], manifest_packages)
            self.log.info("successfully parsed manifest")
        else:
            self.log.error("couldn't determine manifest")
            self.write_log(fail_log_path, stderr=errors)
            self.database.set_image_requests_status(self.params["request_hash"], "manifest_fail")
            return False

        # set directory where image is stored on server
        self.image.set_image_dir()
        self.log.debug("dir %s", self.image.params["dir"])

        # calculate hash based on resulted manifest
        self.image.params["image_hash"] = get_hash(" ".join(self.image.as_array("manifest_hash")), 15)

        # set log path in case of success
        success_log_path = self.image.params["dir"] + "/buildlog-{}.txt".format(self.params["image_hash"])

        # set build_status ahead, if stuff goes wrong it will be changed
        self.build_status = "created"

        # check if image already exists
        if not self.image.created():
            self.log.info("build image")
            with tempfile.TemporaryDirectory(dir=self.config.get_folder("tempdir")) as build_dir:
                # now actually build the image with manifest hash as EXTRA_IMAGE_NAME
                self.params["worker"] = self.location
                self.params["BIN_DIR"] = build_dir
                self.params["j"] = str(os.cpu_count())
                self.params["EXTRA_IMAGE_NAME"] = self.params["manifest_hash"]
                # if uci defaults are added, at least at parts of the hash to time image name
                if self.params["defaults_hash"]:
                    defaults_dir = build_dir + "/files/etc/uci-defaults/"
                    # create folder to store uci defaults
                    os.makedirs(defaults_dir)
                    # request defaults content from database
                    defaults_content = self.database.get_defaults(self.params["defaults_hash"])
                    with open(defaults_dir + "99-server-defaults", "w") as defaults_file:
                        defaults_file.write(defaults_content) # TODO check if special encoding is required

                    # tell ImageBuilder to integrate files
                    self.params["FILES"] = build_dir + "/files/"
                    self.params["EXTRA_IMAGE_NAME"] += "-" + self.params["defaults_hash"][:6]

                # download is already performed for manifest creation
                self.params["NO_DOWNLOAD"] = "1"

                build_start = time.time()
                return_code, buildlog, errors = self.run_meta("image")
                self.image.params["build_seconds"] = int(time.time() - build_start)

                if return_code == 0:
                    # create folder in advance
                    os.makedirs(self.image.params["dir"], exist_ok=True)

                    self.log.debug(os.listdir(build_dir))

                    for filename in os.listdir(build_dir):
                        if os.path.exists(self.image.params["dir"] + "/" + filename):
                            break
                        shutil.move(build_dir + "/" + filename, self.image.params["dir"])

                    # possible sysupgrade names, ordered by likeliness
                    possible_sysupgrade_files = [ "*-squashfs-sysupgrade.bin",
                            "*-squashfs-sysupgrade.tar", "*-squashfs.trx",
                            "*-squashfs.chk", "*-squashfs.bin",
                            "*-squashfs-sdcard.img.gz", "*-combined-squashfs*",
                            "*.img.gz"]

                    sysupgrade = None

                    for sysupgrade_file in possible_sysupgrade_files:
                        sysupgrade = glob.glob(self.image.params["dir"] + "/" + sysupgrade_file)
                        if sysupgrade:
                            break

                    if not sysupgrade:
                        self.log.debug("sysupgrade not found")
                        if buildlog.find("too big") != -1:
                            self.log.warning("created image was to big")
                            self.database.set_image_requests_status(self.params["request_hash"], "imagesize_fail")
                            self.write_log(fail_log_path, buildlog, errors)
                            return False
                        else:
                            self.build_status = "no_sysupgrade"
                            self.image.params["sysupgrade"] = ""
                    else:
                        self.image.params["sysupgrade"] = os.path.basename(sysupgrade[0])

                    self.write_log(success_log_path, buildlog)
                    self.database.add_image(self.image.get_params())
                    self.log.info("build successfull")
                else:
                    self.log.info("build failed")
                    self.database.set_image_requests_status(self.params["request_hash"], 'build_fail')
                    self.write_log(fail_log_path, buildlog, errors)
                    return False

        self.log.info("link request %s to image %s", self.params["request_hash"], self.params["image_hash"])
        self.database.done_build_job(self.params["request_hash"], self.image.params["image_hash"], self.build_status)
        return True

    def run(self):
        self.setup_meta()

        while True:
            self.params = self.queue.get()
            self.version_config = self.config.version(
                    self.params["distro"], self.params["version"])
            if self.job == "image":
                self.build()
            elif self.job == "update":
                self.info()
                self.parse_packages()

    def info(self):
        self.parse_info()
        if os.path.exists(os.path.join(
                self.location, "imagebuilder",
                self.params["distro"], self.params["version"],
                self.params["target"], self.params["subtarget"],
                "target/linux", self.params["target"],
                "base-files/lib/upgrade/platform.sh")):
            self.log.info("%s target is supported", self.params["target"])
            self.database.insert_supported(self.params)

    def run_meta(self, cmd):
        cmdline = ["sh", "meta", cmd ]
        env = os.environ.copy()

        if "parent_version" in self.version_config:
            self.params["IB_VERSION"] = self.version_config["parent_version"]
        if "repos" in self.version_config:
            self.params["REPOS"] = self.version_config["repos"]

        for key, value in self.params.items():
            env[key.upper()] = str(value) # TODO convert meta script to Makefile

        proc = subprocess.Popen(
            cmdline,
            cwd=self.location,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            env=env
        )

        output, errors = proc.communicate()
        return_code = proc.returncode
        output = output.decode('utf-8')
        errors = errors.decode('utf-8')

        return (return_code, output, errors)


    def parse_info(self):
        self.log.debug("parse info")

        return_code, output, errors = self.run_meta("info")

        if return_code == 0:
            default_packages_pattern = r"(.*\n)*Default Packages: (.+)\n"
            default_packages = re.match(default_packages_pattern, output, re.M).group(2)
            logging.debug("default packages: %s", default_packages)

            profiles_pattern = r"(.+):\n    (.+)\n    Packages: (.*)\n"
            profiles = re.findall(profiles_pattern, output)
            if not profiles:
                profiles = []
            self.database.insert_profiles({
                "distro": self.params["distro"],
                "version": self.params["version"],
                "target": self.params["target"],
                "subtarget": self.params["subtarget"]},
                default_packages, profiles)
        else:
            logging.error("could not receive profiles")
            return False

    def parse_packages(self):
        self.log.info("receive packages")

        return_code, output, errors = self.run_meta("package_list")

        if return_code == 0:
            packages = re.findall(r"(.+?) - (.+?) - .*\n", output)
            self.log.info("found {} packages".format(len(packages)))
            self.database.insert_packages_available({
                "distro": self.params["distro"],
                "version": self.params["version"],
                "target": self.params["target"],
                "subtarget": self.params["subtarget"]}, packages)
        else:
            self.log.warning("could not receive packages")

class Updater(threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self)
        self.log = logging.getLogger(__name__)
        self.config = Config()
        self.database = Database(self.config)
        self.update_queue = Queue(1)

    def run(self):
        location = self.config.get("updater_dir", "updater")
        Worker(location, None, None).setup_meta()
        workers = []

        # start all workers
        for i in range(0, self.config.get("updater_threads", 4)):
                worker = Worker(location, "update", self.update_queue)
                worker.start()
                workers.append(worker)

        while True:
            outdated_subtarget = self.database.get_subtarget_outdated()
            if outdated_subtarget:
                log.info("found outdated subtarget %s", outdated_subtarget)
                self.update_queue.put(outdated_subtarget)
            else:
                time.sleep(5)

class Boss(threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self)
        self.log = logging.getLogger(__name__)
        self.config = Config()
        self.database = Database(self.config)
        self.build_queue = Queue(1)

    def run(self):
        workers = []
        for worker_location in self.config.get("workers"):
            worker = Worker(worker_location, "image", self.build_queue)
            worker.start()
            workers.append(worker)

        self.log.info("Active workers are %s", workers)

        while True:
            build_job = self.database.get_build_job()
            if build_job:
                self.log.info("Found build job %s", build_job)
                self.build_queue.put(build_job)
            else:
                time.sleep(10)

if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    log = logging.getLogger(__name__)
    log.info("start garbage collector")
    gaco = GarbageCollector()
    gaco.start()

    log.info("start boss")
    boss = Boss()
    boss.start()

    log.info("start updater")
    uper = Updater()
    uper.start()

    # TODO reimplement
    #def diff_packages(self):
    #    profile_packages = self.vanilla_packages
    #    for package in self.packages:
    #        if package in profile_packages:
    #            profile_packages.remove(package)
    #    for remove_package in profile_packages:
    #        self.packages.append("-" + remove_package)

