$ErrorActionPreference = 'Stop'
$usbDevices = @(Get-PnpDevice -PresentOnly | Where-Object InstanceId -Match '^USB\\VID_2E8A&PID_(0003|000A)\\[A-Fa-f0-9]+$')
$ports = @(Get-PnpDevice -PresentOnly -Class Ports -ErrorAction SilentlyContinue)
$disks = @(Get-CimInstance Win32_DiskDrive | Where-Object PNPDeviceID -Like 'USBSTOR\*')
$result = foreach ($device in $usbDevices) {
    $location = @((Get-PnpDeviceProperty -InstanceId $device.InstanceId -KeyName DEVPKEY_Device_LocationPaths).Data)[0]
    $com = $null
    foreach ($port in $ports) {
        $parent = (Get-PnpDeviceProperty -InstanceId $port.InstanceId -KeyName DEVPKEY_Device_Parent -ErrorAction SilentlyContinue).Data
        if ($parent -eq $device.InstanceId -and $port.FriendlyName -match '\((COM\d+)\)') { $com = $Matches[1] }
    }
    $volumes = @()
    foreach ($disk in $disks) {
        $ancestor = $disk.PNPDeviceID
        for ($i = 0; $i -lt 8 -and $ancestor; $i++) {
            if ($ancestor -eq $device.InstanceId) {
                foreach ($partition in @(Get-CimAssociatedInstance -InputObject $disk -Association Win32_DiskDriveToDiskPartition)) {
                    foreach ($volume in @(Get-CimAssociatedInstance -InputObject $partition -Association Win32_LogicalDiskToPartition)) {
                        $volumes += $volume.DeviceID + '\'
                    }
                }
                break
            }
            $ancestor = (Get-PnpDeviceProperty -InstanceId $ancestor -KeyName DEVPKEY_Device_Parent -ErrorAction SilentlyContinue).Data
        }
    }
    [PSCustomObject]@{InstanceId=$device.InstanceId; FriendlyName=$device.FriendlyName; Location=$location; Com=$com; Volumes=@($volumes)}
}
ConvertTo-Json -InputObject @($result) -Depth 4 -Compress
