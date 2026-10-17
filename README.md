# Blender-Plugin-OptiTrack

OptiTrack plugin for the rendering engine Blender.



## How to install the Plugin

In order to the install the Plugin:

1. Download the latest version of Blender.
2. Open Blender, and Install it in Edit > Preferences > Add-ons.
3. Search for the "OptiTrack: Motive Blender Plugin".
4. Once Added, you can then close the Add-ons window and Press N to Open the Motive Panel where the Plugin functionality is.



## How to Start Developing

In order to start developing on the Blender plugin it is helpful to be working with the latest code base hosted on GitHub. To do this:

1. Download the latest version of Blender.
2. Gain access to the OptiTrack Organization and the Blender Team/ Repo on GitHub.
3. Clone the Repo on your Machine to this Location: C:\\Users"Your Account"\\AppData\\Roaming\\Blender Foundation\\Blender\\4.x or 5.x\\scripts\\addons\\
4. Fetch the Dev Branch or Create and Fetch a Branch associated to a Ticket you’re working on .
5. Open Blender and enable the plugin by going to Edit > Preferences > Add-ons and Search for the OptiTrack: Motive Blender Plugin.
6. Once Added, you can then close the Add-ons window and Press N to Open the Motive Panel where the Plugin functionality is.


## Real-Time Retargeting for Custom Humanoid Armatures

The plugin includes a real-time retargeting subsystem located in Blender's **Object Properties** panel (`Motive: Assign Assets`). It allows streaming NatNet mocap data onto arbitrary humanoid rigs (e.g., Mixamo, Unreal Engine Mannequin, Rigify) with automatic bone detection and parent-relative Forward Kinematics.

For full mathematical formulations, architecture diagrams, and verification benchmarks, see:
- **[Technical Specification & Retargeting Architecture](RETARGETING_SYSTEM.md)**
