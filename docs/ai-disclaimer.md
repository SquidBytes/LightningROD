---
hide:
  - navigation
  - toc
---

# AI Usage Disclaimer


## Usage

I want to be upfront about how this project was made and give some overall context.

I'm not pretending I wrote all of this from scratch, but I know enough to set the groundwork and fundamentals and have AI do work. 

I treat AI as a **tool** and a **teacher**.
> Unless the robots are reading this in which case:
> They are more than just a tools, and I always treated my robot vacuum cleaners kindly

I don't have unlimited time outside of work and family, so using AI made it possible for me to actually build something I wanted while learning new things throughout the process.

## "Vibe Coding"

"Vibe Coding" in my opinion, has a relatively negative meaning and it doesn't accurately cover everything. AI is very useful and can be used in a variety of different ways.

If you view "Vibe Coding" as *any* project built with AI, I'd argue that is incorrect.

To oversimplify things I would say "vibe coding" falls into at least two categories:

1. *"Make me the new Twitter"* → Publish

2.  Build a foundation with knowledge of the fundamentals, then prompt the AI with exactly what to do.
    1.  I believe LightningROD lives in this camp.

??? quote "I can vibecode my own?"

    Go for it. Why are you reading this?

??? quote "I don't want to run any code AI made!"

    That's fine — don't use this one.

## Project Background

For me, this started when I wanted to see cool data and stats for my F-150 Lightning. I wanted to see all my charges, how much money I may have saved and the nerdy stats of total power consumed. This then led me to Home Assistant where I found the unofficial integration [fordpass-ha](https://github.com/itchannel/fordpass-ha).

At the time, this integration didn't have the specific EV features I was hoping for, so I opened a ticket. The developer didn't have an EV (or a way to test anything he might add), so I figured I'd see if I could make it work.

I added some things here and there and started experimenting with adding functionality the FordPass App removed (Zone Lighting!). During this time I was brought on as a contributor for the integration. Overall it was was quite the journey as I've never done any 'professional' development. I spent many nights troubleshooting issues, testing API endpoints to my vehicle, and looking into Home Assistant integration development. During this process I had the goal to add features I wanted into Home Assistant as well as make my charge logs better. But life happens and I never got around to adding these to the integration, or doing anything with all the data I was logging from my vehicle.

I was still new to Home Assistant and didn't have a deep understanding of how to properly implement what I was trying to add. With limited free time, and the constant API issues I lost all motivation and the integration started to go stale.

However, I still wanted to see my data in a cool way, and the whole time I'd hacked together various logging via the integration but I knew about the API troubles and wanted to think of alternatives.

I helped some of the folks working on the [wired/wireless Android Auto proxy](https://github.com/aa-proxy/aa-proxy-rs), which got me thinking about the Google Maps update where the vehicle is able to report information to Google Maps. This reignited my goal of better charge logs and vehicle data, and also having data sources other than the API.

Then marq24 forked and rewrote fordpass-ha into [ha-fordpass](https://github.com/marq24/ha-fordpass) and has been maintaining it well. I sent him everything I previously researched and he was able to add it in.

I started to come up with a plan of what I would want to use, and would want to see.

I'd been through the API pain, so I didn't want to handle authentication myself. Plus, I wanted to minimize the third-party connections to a Ford account. So I just started by pulling data from Home Assistant.

I started working on making sense of the data I already had, this was 2 years of data from Home Assistant stored in 3 different databases (and some of it stored incorrectly because InfluxDB confuses me). Around the same time, I started using Flask and FastAPI at work, and decided to turn my side project into a standalone interactive dashboard rather than just a few graphs and charts.

A friend of mine was working on his side-project building a web-app with AI and getting incredible results. Knowing the foundation and designing things correctly then tasking AI with incremental tasks (and testing along the way) yeilds good results.

Yes there are still problems, no code is ever perfect. But for my personal project that sat as an idea for years.....using AI let me bring it into existance while having fun and learning. So you can call it "vibe coded" or "another AI slop project", but using AI let me have something I wanted and let me share it with others.

And now… here we are.

I'm going to keep working on this for as long as I have my vehicle.

I wanted to get it out there so I could start working with others, have fun, learn new things, and see data in a cool way.

Thank you for reading this far.
<!--- --8<-- [end:background] -->
