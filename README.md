# <img src='./ui/soundcloud.png' width='50' height='50' style='vertical-align:bottom'/> Soundcloud Skill

soundcloud skill for OCP

## About

search soundcloud by voice!

![](./gui.png)

## Examples
* "play piratech in soundcloud"
* "play piratech nuclear chill"

## Settings

you can add queries to skill settings that will then be pre-fetched on skill load

this populates the featured_media entries + provides fast matching against cached entries

```javascript
{    
"featured_tracks" : ["piratech nuclear chill"],
"featured_artists":  ["rob zombie", "metallica", "piratech"],
"featured_sets": ["jazz", "classic rock"]
}
```

a local cache of entries can be found at `~/.cache/OCP/Soundcloud.json`

## Credits
JarbasAl

## Category
**Entertainment**

## Tags
- soundcloud
- OCP
- common play
- music
