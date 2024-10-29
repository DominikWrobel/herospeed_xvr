# Readme

![herospeed-icon](https://github.com/user-attachments/assets/e13efbf1-65b2-430d-9e76-0beaf3d8a629)

### Herospeed XVR
This is a simple integration for getting motion sensors from Herospeed XVR cameras to show up in Home Assistant. In order to make this integration work you have to set up an e-mail serwer on your Herospeed XVR and set the SMTP server to custom and add the ip adress of your Home Assistant instance. Then choose a port and for encryption use NONE set a user name and password.

![xvr setup](https://github.com/user-attachments/assets/f3bce5b9-0ff1-4e90-b640-4942e174a055)

In Home Assistant you have to provide the ip adress of your XVR, SMTP port, number of Camera Channels (important, this integration reades e-mail headers sent, so if motion is detected on channel 4 you have to set up 4 cameras, if you have 16 cameras but motion only on 1, 5 and 16 you have to add 16 cameras and then find the motion sensors you want), Motion Reset Delay, SMTP user name and password.

![hasetup](https://github.com/user-attachments/assets/58b9adb0-fcb5-484d-b19f-720004417101)

Now you will have motion sensors set up for your cameras and when the XVR finds motion it will send an e-mail which will be read by this integration and shown as motion in Home Assistant, make sure you set up motion sensors for your cammeras in the XVR first.

![sensors](https://github.com/user-attachments/assets/149233cd-d45a-48d1-b9cd-7d6cc281388f)

I do not plan to add any other sensors to this integration, if you want live stream from the camera use rstp adresses.

# Install with HACS recomended:
It is possible to add it as a custom repository.

If you are using HACS, go to HACS -> Integrations and click on the 3 dots (top righ corner).
Then choose custom repositories, and add this repository url (https://github.com/DominikWrobel/herospeed_xvr), choosing the Integration category.

That's it, and you will be notified by HACS on every release.
Just click on upgrade.

# Support

If you like my work you can support me via:

<figure class="wp-block-image size-large"><a href="https://www.buymeacoffee.com/dominikjwrc"><img src="https://homeassistantwithoutaplan.files.wordpress.com/2023/07/coffe-3.png?w=182" alt="" class="wp-image-64"/></a></figure>
