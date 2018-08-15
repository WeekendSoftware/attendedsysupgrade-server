import threading
from queue import Queue
import logging
import time

from utils.image import Image
from utils.common import get_hash
from utils.config import Config
from utils.database import Database
from utils.worker import Worker

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
