---
hide:
  - navigation
---

# How LightningROD Started

LightningROD is a personal project. I built it because I wanted it, and I share it because I love open source and I thought some people might want it and enjoy it.

I use AI to build it. That gets its own page: [AI Usage Disclaimer](ai-disclaimer.md).

## The short version

| When | What |
|---|---|
| Feb 2023 | Got an F-150 Lightning, my first EV. Wanted TeslaMate-style stats. Found nothing. |
| 2023 | Found Home Assistant and the unofficial [fordpass-ha](https://github.com/itchannel/fordpass-ha) integration. Opened [my first ever pull request](https://github.com/itchannel/fordpass-ha/pull/297) to pull charge logs. |
| Sept 2023 | First LightningROD repo: scripts piggybacking off fordpass-ha, logging to Postgres, with a Grafana dashboard to make it pretty. |
| 2023–2024 | Became a contributor on fordpass-ha. Added the EV features. Dug out Zone Lighting, charge start/stop, heated/vented seats and wheel. |
| 2024 | Ford's API kept breaking. Burnout. The integration went stale. So did LightningROD. Meanwhile my data was a mess (Postgres → InfluxDB → VictoriaMetrics), stored wrong because Influx and VictoriaMetrics confuse me. |
| Jun 2025 | [marq24](https://github.com/marq24) forked and updated it as [ha-fordpass](https://github.com/marq24/ha-fordpass). I sent him everything I had so far. |
| Feb 2026 | Started the rewrite that became today's LightningROD: a real web app instead of a Grafana dashboard. |
| Mar 2026 | First public release. |
| Oct 2026 | `0.6.0` adds Ford's official FordConnect API, so users have options for hosting |

## The long version

### The start

This all started when I got my Lightning in February 2023. It's my first ever EV (and it's great). I have friends and co-workers with Teslas and I've seen some of their cool statistics like: energy used, total regen (energy and miles), money saved, charging speeds, all of it. I love open source and self-hosting, so I went looking for something like TeslaMate or TeslaFi, but for my vehicle... or even just for Ford.

Nothing like that existed. The FordPass app only displayed the last 20 charges, with no way to export them. This annoyed me. The truck is "smart", it's my data, and I wanted to keep it and use it how I wanted. That is how everything began.

### Home Assistant and fordpass-ha

The rabbit hole started with trying to automate downloading those charge logs...somehow. I tried to think of many different options or alternative options. Most of those were going to be a difficult task. However while researching custom telematics and solutions around this "problem" I kept running into Home Assistant. I'd known about it before, but I thought it was just a self-hosted thing for smart IoT devices. The more I learned, the more I fell in love. I still think Home Assistant is one of the coolest pieces of software I've used.

As I used Home Assistant more, I fell into that rabbit hole and discovered HACS (which is its own rabbit hole haha). HACS had the unofficial fordpass-ha integration by itchannel. I set that up to try to get these charge logs, but after installing and looking at everything I found they didn't exist, and there weren't really any EV features that could exist. But it had already handled the painful part... authentication.

That got me thinking... these integrations are written in Python, and I know some Python. If I piggybacked off this integration and hit the same API endpoint the app used for charge logs, I could just download them myself. I tried it and got it working. Then I had the thought "oh, this is open source, I wonder if someone else could use this", so I opened a pull request. This pull request was also my first time really using GitHub and my first PR for anything outside of work. It can still be viewed/roasted here https://github.com/itchannel/fordpass-ha/pull/297

As I got familiar with the data the vehicle provides, and how the integration turned that data into sensors, I started adding things in and tried to add more things to the pull request. itchannel brought me on as a contributor and I added the missing EV features. I then closed my own PR since those were added into other sensors and integrated in more with what existed (that's why there were so many attributes if you used it during that time). While working on this integration I was still new to just using Home Assistant in general, and just new to working on a public repo. I was self-taught for a lot of it and just trying to follow the conventions in the file I was working with. This is also why those attributes I mentioned had the horrible naming conventions and were so random. And I made the stupid mistake of putting a space in their names (I have learned my lesson, that was dumb).

After all of that, my priority and goal was to add in the heavily requested charge controls. I also really wanted to bring back features Ford removed from the app (ZONE LIGHTING! Which is still missing from the app).

None of that was documented anywhere. Figuring out how this worked. How the app got this data from the vehicle, what that data is and what I can do with it, took many nights of testing against my own truck and trying different combinations of things. This was the long and difficult part, and it's the part I'm most proud of. Finding oddities like the `CANCEL` command actually starting the charge was a fun puzzle. [I sent marq24 everything I found and exchanged all of the information I had and he was able to add them in and make them better](https://github.com/marq24/ha-fordpass/discussions/39).


### The stale period

If you used fordpass-ha during that time, you know about the API issues. Ford changed things constantly. Between that, limited free time, and still not understanding Home Assistant deeply enough to build what I wanted properly, I burned out and the integration went stale.

The whole time I was still logging my own data with a locally modified copy of the integration. I started with a Postgres database because that's what I knew. While reading documentation and forums I kept seeing InfluxDB be recommended, so I switched. That broke a lot of things, and when I "got it working" I left it alone for a long time. The next time I went to look at my data, everything was logged wrong, because their query syntax confuses me and I added in the connection wrong. This is also when I found out that those damn spaces I mentioned above really started to be a problem. While attempting to make sense of InfluxDB's query syntax, I found VictoriaMetrics, which "works great with Home Assistant" and "makes things easier" so.... I switched again, and repeated the same mistakes. So: three databases, years of sensor data, mostly stored incorrectly and my past mistakes (SPACES!!) still haunting me. I had no idea where, or how, to start piecing it together. All while the API kept changing and life kept happening.

### Side quests

Somewhere in there I helped some of the folks working on the [wired/wireless Android Auto proxy](https://github.com/aa-proxy/aa-proxy-rs). This got me thinking about the "pending/future" Ford Sync update integrating vehicle battery data into Google Maps. This reignited the idea that had been burning a hole in my brain for years..... better charge logs and possibly some additional information without being tethered to the API. There were possible data source options I could use to get my data.

### The rewrite

I'd been through the API pain, I didn't want to handle authentication myself, and I wanted to avoid being dependent upon Ford servers and their API. But the fastest way to get that data would be to start by using what exists, or by using that API. I knew about possible account lock outs due to "third party integrations" so I needed to avoid that possibility. ha-fordpass uses websockets to get its data, and while reading about that I thought: why not use the same kind of connection, but to Home Assistant, since everything is already handled there?

Around the same time I started helping with a project at work that used Flask. A friend was building his own side project with AI and getting incredible results, especially having the knowledge and understanding of the foundation. With his help, and AI's, I finally got my years of badly stored data sorted out. Then I decided the "few graphs and charts" should become a standalone dashboard and I should use as much of the data as I could. Being a standalone web app also opens up options to support multiple data sources which is still my intent and goal. This was the start of the "rewrite" (even though the very first thing was just a script or two) in February 2026.


### Now

In `0.6.0` I added Ford's official FordConnect API, which didn't exist when I started. It's also the second data source option (but still dependent upon Ford's servers). I'll give Ford credit for that one, I'm happy they added this.

Personally I've never wanted to get rich or famous off this. I get satisfaction from knowing other people are using something I made, and that the time I spend on it helps someone other than me. At the very least I'm learning a ton of new things and having fun doing so.

I'm going to keep working on this for as long as I have my vehicle, but I have limited free time. I'm doing my best to avoid having this become an abandoned "vibe" project, but I'm not perfect and don't know everything so some features I lean more on AI than others. Anyway, rambling story aside I hope this story shows that LightningROD is more than just a quick "vibe coded" weekend project for stars.... or something other project desire. I've spent a considerable amount of time and effort working on this, even if I didn't write all the code myself. It's not perfect, but that is what my hope is for making it open source. To make it better.

Thank you for your time and for reading this far.
I hope you can get some usage from LightningROD

- SquidBytes
