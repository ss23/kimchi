/*
 * Project Kimchi
 *
 * Copyright IBM, Corp. 2013-2014
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
kimchi.template_edit_main = function() {
    var templateEditForm = $('#form-template-edit');
    var origDisks;
    var origPool;
    var templateDiskSize;
    $('#template-name', templateEditForm).val(kimchi.selectedTemplate);
    kimchi.retrieveTemplate(kimchi.selectedTemplate, function(template) {
        origDisks =  template.disks;
        origPool = template.storagepool;
        for(var i=0;i<template.disks.length;i++){
            if(template.disks[i].base){
                template["vm-image"] = template.disks[i].base;
                $('#templ-edit-cdrom').addClass('hide-content');
                $('#templ-edit-vm-image').removeClass('hide-content');
                break;
            }
        }
        for ( var prop in template) {
            var value = template[prop];
            if (prop == 'graphics') {
               value = value["type"];
            }
            $('input[name="' + prop + '"]', templateEditForm).val(value);
        }
        var disks = template.disks;
        $('input[name="disks"]').val(disks[0].size);
        templateDiskSize = $('input[name="disks"]').val();
        if (disks[0].volume) {
            var spool_value = $('#form-template-edit [name="storagepool"]').val();
            $('input[name="storagepool"]', templateEditForm).val(spool_value + '/' + disks[0].volume);
            $('input[name="disks"]', templateEditForm).attr('disabled','disabled');
        }

        var vncOpt = [{label: 'VNC', value: 'vnc'}];
        kimchi.select('template-edit-graphics-list', vncOpt);
        var enableSpice = function() {
            if (kimchi.capabilities == undefined) {
                setTimeout(enableSpice, 2000);
                return;
            }
            if (kimchi.capabilities.qemu_spice == true) {
                spiceOpt = [{label: 'Spice', value: 'spice'}]
                kimchi.select('template-edit-graphics-list', spiceOpt);
            }
        };
        enableSpice();

        var scsipools = {};
        kimchi.listStoragePools(function(result) {
            var options = [];
            if (result && result.length) {
                $.each(result, function(index, storagePool) {
                    if ((storagePool.state=="active") && (storagePool.type !== 'kimchi-iso')) {
                        if ((storagePool.type == 'iscsi') || (storagePool.type == 'scsi')){
                            scsipools[storagePool.name] = [];
                            kimchi.listStorageVolumes(storagePool.name, function(result) {
                                if (result && result.length) {
                                    $.each(result, function(index, storageVolume) {
                                        options.push({
                                            label: storagePool.name + '/' + storageVolume.name,
                                            value: '/storagepools/' + storagePool.name + '/' + storageVolume.name
                                        });
                                        scsipools[storagePool.name].push(storageVolume)
                                    });
                                }
                                kimchi.select('template-edit-storagePool-list', options);
                            });
                        }
                        else {
                            options.push({
                                label: storagePool.name,
                                value: '/storagepools/' + storagePool.name
                            });
                        }
                    }
                });
            }
            if ($.isEmptyObject(scsipools)) {
                kimchi.select('template-edit-storagePool-list', options);
            }
        });
        kimchi.listNetworks(function(result) {
            if(result && result.length > 0) {
                var html = '';
                var tmpl = $('#tmpl-network').html();
                $.each(result, function(index, network) {
                    if (result[index].state === 'active')
                        html += kimchi.substitute(tmpl, network);
                });
                $('#template-edit-network-list').html(html).show();
                if(template.networks && template.networks.length > 0) {
                    $('input[name="networks"]', templateEditForm).each(function(index, element) {
                        var value = $(element).val();
                        if(template.networks.indexOf(value) >= 0) {
                            $(element).prop('checked', true);
                        }
                    });
                }
            } else {
                $('#template-edit-network-list').hide();
            }
        });
    });

    $('#template-edit-storagePool').change(function() {
        storagepool = $(this).val();
        var storageArray = storagepool.split("/");
        if (storageArray.length > 3) {
            volumeName = storageArray.pop();
            poolName = storageArray.pop();
            kimchi.getStoragePoolVolume(poolName, volumeName, function(result) {
                $('input[name="disks"]', templateEditForm).val(result.capacity / Math.pow(1024,3));
                $('input[name="disks"]', templateEditForm).attr('disabled','disabled');
                return false;
            }, function (err) {
                kimchi.message.error(err.responseJSON.reason);
            });
        } else {
            $('input[name="disks"]', templateEditForm).removeAttr('disabled');
            $('input[name="disks"]', templateEditForm).val(templateDiskSize);
        }
    });
    $('input[name="disks"]', templateEditForm).keyup(function() {
        templateDiskSize = $('input[name="disks"]', templateEditForm).val();
    });

    $('#tmpl-edit-button-save').on('click', function() {
        var editableFields = [ 'name', 'cpus', 'memory', 'storagepool', 'disks', 'graphics'];
        var data = {};
        $.each(editableFields, function(i, field) {
            /* Support only 1 disk at this moment */
            if (field == 'disks') {
               origDisks[0].size = Number($('#form-template-edit [name="' + field + '"]').val());
               data[field] = origDisks;
            }
            else if (field == 'graphics') {
               var type = $('#form-template-edit [name="' + field + '"]').val();
               data[field] = {'type': type};
            }
            else {
               data[field] = $('#form-template-edit [name="' + field + '"]').val();
            }
        });
        data['memory'] = Number(data['memory']);
        data['cpus']   = Number(data['cpus']);
        storagepool = data['storagepool'];
        storageArray = storagepool.split("/");
        if (storageArray.length > 3){
            /* Support only 1 disk at this moment */
            data["disks"][0].volume = storageArray.pop();
            data['storagepool'] = storageArray.join("/");
        } else if (data["disks"][0].volume) {
            delete data["disks"][0].volume;
        }
        var networks = templateEditForm.serializeObject().networks;
        if (networks instanceof Array) {
            data.networks = networks;
        } else if (networks != null) {
            data.networks = [networks];
        } else {
            data.networks = [];
        }

        kimchi.updateTemplate($('#template-name').val(), data, function() {
            kimchi.doListTemplates();
            kimchi.window.close();
        }, function(err) {
            kimchi.message.error(err.responseJSON.reason);
        });
    });
};