Vagrant.configure("2") do |config|
  config.vm.box = "debian/stretch64"
  config.vm.box_check_update = false
  config.vm.provision "ansible" do |ansible|
    ansible.playbook = "ansible/vagrant.yml"
  end
end
