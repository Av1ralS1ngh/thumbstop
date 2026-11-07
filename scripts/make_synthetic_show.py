"""Write ORIGINAL fictional microdramas in the Kuku screenplay format, for tests and interview rehearsal.

  python scripts/make_synthetic_show.py

tests/fixtures/new_show/   "Glass Ledger", a 10-episode office-revenge drama (60-120 s per episode). The formatting
                           drifts on purpose: upper-case scene fields (ep 2), HH:MM:SS.ss timestamps (ep 3), CRLF
                           line endings (ep 4), no continuity notes (ep 5), inline [laughs] tags (ep 6), no CAMERA
                           blocks (ep 7), stacked speaker parentheticals (ep 8), '**LABEL**:' plus blank lines
                           (ep 9), UTF-8 BOM (ep 10).
tests/fixtures/mini_show/  "Ember Throne", 2 short fantasy-court episodes for fast tests.
tests/fixtures/subs_show/  Glass Ledger eps 1-3 delivered as SRT, WebVTT and a timed transcript.

Every story, name and line here is invented. Timings come from word counts, so episodes stay consistent.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "tests" / "fixtures"

# A beat is (label, text): "SCENE" starts a scene (text = heading), "CAM"/"ACT"/"SFX" are camera, action and
# sound blocks, anything else is a speaker label such as "NADIA (Cold, Furious)".
Beats = list[tuple[str, str]]


@dataclass
class Ep:
    num: int
    logline: str
    continuity: str
    beats: Beats
    style: set[str] = field(default_factory=set)


@dataclass
class Block:
    label: str
    t0: int     # centiseconds
    t1: int
    text: str
    scene: int


GLASS_LEDGER = [
    Ep(1, "Junior auditor Nadia Okonjo exposes a pension theft at the company gala, and the CFO frames her for it.",
       "Opening episode.", [
        ("SCENE", "INT. MERIDIAN CREST TOWER – GALA BALLROOM – NIGHT"),
        ("CAM", "Wide shot of a glittering corporate gala; a banner reads MERIDIAN CREST: TWENTY YEARS OF TRUST."),
        ("ACT", "Nadia, in a borrowed black dress, pushes through the crowd clutching a printed spreadsheet."),
        ("NADIA (Urgent, Low)", "Mr. Voss, four million dollars left the pension fund last night. I traced it."),
        ("CAM", "Close-up on Grant Voss, his smile freezing."),
        ("GRANT (Smooth, Cold)", "Not here, Miss Okonjo. Smile. The board is watching."),
        ("NADIA (Defiant)", "Then let them watch. The transfer has your approval code on it."),
        ("ACT", "Grant grabs the spreadsheet from her hand and raises it high above his head."),
        ("GRANT (Shouting)", "Everyone! Our junior auditor has been stealing from the pension fund!"),
        ("SFX", "The music cuts out. A glass shatters on the marble floor."),
        ("CAM", "Slow push-in on Nadia as two hundred faces turn toward her."),
        ("NADIA (Shocked)", "That's a lie! I found the theft, I didn't commit it!"),
        ("ACT", "Selene March steps out of the crowd in a white gown and slaps Nadia across the face."),
        ("SELENE (Cruel, Mocking)", "Ten years of friendship, Nadia, and you rob the people who fed you?"),
        ("NADIA (Horrified)", "Selene... you know me. You know I would never do this."),
        ("GRANT (Commanding)", "Security! Get her out of my building!"),
        ("ACT", "Two guards seize Nadia's arms and drag her toward the service exit as phones record her."),
        ("SFX", "Camera shutters click like rain."),
        ("SCENE", "EXT. MERIDIAN CREST TOWER – LOADING DOCK – NIGHT"),
        ("CAM", "Overhead shot of rain hammering the loading dock."),
        ("ACT", "The guards shove Nadia onto the wet concrete and slam the steel door behind her."),
        ("NADIA (Broken, Whispering)", "Twenty years of trust. What a joke."),
        ("ACT", "A hooded man steps out of the shadows holding an umbrella. It is Theo Kellan, the quiet IT contractor."),
        ("THEO (Calm)", "You weren't wrong about the four million. It was only the first transfer."),
        ("NADIA (Suspicious)", "Who are you? Why would the IT guy know that?"),
        ("CAM", "Close-up on a silver USB drive in Theo's palm."),
        ("THEO (Quiet, Urgent)", "Because I copied his private ledger before he wiped it. There are eleven more."),
        ("NADIA (Desperate)", "Then give it to the police!"),
        ("THEO (Grim)", "The police chief was at that gala. Server room, before midnight, or the backups are gone forever."),
        ("SFX", "An ominous sting as the tower lights go dark floor by floor."),
        ("CAM", "Hold on Nadia's face, rain and mascara running."),
    ]),
    Ep(2, "Nadia and Theo break into the server room to save the backups and overhear Grant ordering her silenced.",
       "Picking up at the Episode 1 cliffhanger: Theo gives Nadia a midnight deadline at the server room.", [
        ("SCENE", "INT. MERIDIAN CREST TOWER – SERVER ROOM – NIGHT"),
        ("CAM", "Blue server lights blink in long rows; a wall clock reads 11:52."),
        ("ACT", "Theo swipes a borrowed key card; the lock flashes green and Nadia slips in behind him."),
        ("NADIA (Whispering)", "Eight minutes. What happens at midnight?"),
        ("THEO (Focused)", "An automatic purge. Every backup older than one day disappears."),
        ("ACT", "Theo plugs the USB into a terminal; columns of transfers scroll across the screen."),
        ("NADIA (Stunned)", "Twelve transfers. Forty-eight million dollars. All to the same shell company."),
        ("THEO (Grim)", "Halcyon Bay Holdings. Look who signed the incorporation papers."),
        ("CAM", "Close-up on the screen: a scanned signature beside the name SELENE MARCH."),
        ("NADIA (Betrayed, Shaking)", "Selene. She slapped me in front of everyone for her own crime."),
        ("SFX", "The elevator chimes in the corridor outside."),
        ("ACT", "Theo yanks Nadia down behind a server rack as the door swings open."),
        ("CAM", "Low angle on polished shoes stepping into the room."),
        ("GRANT (On phone, Cold)", "Yes, the girl is finished. Every newspaper runs the gala video tomorrow."),
        ("GRANT (Menacing)", "But she saw the pension numbers. Make sure the auditor never testifies."),
        ("NADIA (Terrified, Whispering)", "He's talking about killing me."),
        ("THEO (Whispering)", "Don't move. The download needs ninety more seconds."),
        ("ACT", "The progress bar crawls to ninety percent; its glow lights Nadia's hiding spot."),
        ("GRANT (Suspicious)", "Why is this terminal on?"),
        ("CAM", "Extreme close-up on the USB light blinking red."),
        ("ACT", "Grant reaches toward the USB drive, then stops."),
        ("ACT", "Nadia's phone buzzes in her clutch; she smothers it against her chest."),
        ("SFX", "A low buzz, then silence."),
        ("GRANT (Deadly calm)", "I know someone is in here."),
        ("ACT", "Grant clicks on a flashlight and sweeps it slowly along the racks."),
        ("CAM", "The beam stops on the hem of Nadia's black dress."),
        ("GRANT (Menacing)", "Come out now, or I call the men who don't ask questions. Who's there?"),
        ("SFX", "Ominous swell. Cut to black."),
    ], {"upper_fields"}),
    Ep(3, "Theo's fire alarm gets Nadia out of the tower, but the police wait at her mother's door and Selene offers a deal.",
       "Picking up at the Episode 2 cliffhanger: Grant's flashlight finds Nadia's dress behind the rack.", [
        ("SCENE", "INT. MERIDIAN CREST TOWER – SERVER ROOM – NIGHT"),
        ("CAM", "The flashlight beam trembles on the hem of the black dress."),
        ("ACT", "Theo slams his palm onto the fire alarm lever behind the rack."),
        ("SFX", "The fire alarm blares; sprinklers burst on and water rains down."),
        ("GRANT (Furious)", "Stop! Whoever you are, I will find you!"),
        ("ACT", "Nadia rips the USB from the terminal and sprints after Theo through the sprinkler spray."),
        ("THEO (Shouting)", "Stairs, not the elevator! Go!"),
        ("SCENE", "INT. OKONJO FAMILY APARTMENT – KITCHEN – NIGHT"),
        ("CAM", "A small, warm kitchen; a pot of pepper soup steams on the stove."),
        ("ACT", "Nadia stumbles in soaked to the skin; her mother, Funmi, drops a ladle."),
        ("FUNMI (Worried)", "Nadia! The neighbors showed me a video. They are calling you a thief."),
        ("NADIA (Exhausted)", "Mama, it's not true. I can prove it now. I have proof."),
        ("FUNMI (Tender)", "I know my daughter. Sit. Eat something first."),
        ("SFX", "Three heavy knocks on the door."),
        ("OFFICER (O.S.) (Commanding)", "Police! Open the door!"),
        ("ACT", "Nadia shoves the USB into a sugar tin one second before the door bursts open."),
        ("OFFICER (Formal)", "Nadia Okonjo, you are under arrest for the theft of four million dollars."),
        ("FUNMI (Shouting, Desperate)", "No! She is a good girl! Take your hands off her!"),
        ("ACT", "The officer twists Nadia's arms behind her back and snaps on handcuffs."),
        ("CAM", "Close-up on the sugar tin on the shelf."),
        ("SCENE", "INT. CITY POLICE STATION – HOLDING CELL – NIGHT"),
        ("CAM", "Fluorescent light flickers over a concrete bench."),
        ("ACT", "Selene enters in a cream coat, holding a single sheet of paper and a gold pen."),
        ("SELENE (Sweet, Venomous)", "You look terrible. Prison grey was never your color."),
        ("NADIA (Cold)", "I saw your name on Halcyon Bay, Selene. Your signature."),
        ("SELENE (Mocking)", "Then you saw your own death warrant. Sign this confession."),
        ("NADIA (Defiant)", "Never. I'd rather rot in here."),
        ("SELENE (Threatening)", "Then your mother loses her apartment tomorrow. Grant bought the building this afternoon."),
        ("CAM", "Close-up on the gold pen, held out between the bars."),
        ("SELENE (Chilling)", "Sign before sunrise, or she sleeps on the street. What will it be?"),
        ("SFX", "Ominous sting. Hold on Nadia's trembling hand."),
    ], {"hhmmss"}),
    Ep(4, "Nadia tears up the confession, an anonymous bail payment frees her, and Theo admits who he really is.",
       "Picking up at the Episode 3 cliffhanger: Selene demands Nadia sign a confession before sunrise.", [
        ("SCENE", "INT. CITY POLICE STATION – HOLDING CELL – DAWN"),
        ("CAM", "Grey dawn light through a barred window."),
        ("ACT", "Nadia takes the confession, reads it once, and tears it slowly in half."),
        ("NADIA (Cold, Steady)", "Tell Grant he should have framed someone who can't read a ledger."),
        ("SELENE (Enraged)", "You stupid girl! You just made your mother homeless!"),
        ("ACT", "Selene hurls the gold pen at the bars; it ricochets across the floor."),
        ("GUARD (Bored)", "Okonjo. Your bail's been paid. You're free to go."),
        ("SELENE (Stunned)", "Paid? By who? Two million in cash at six in the morning?"),
        ("GUARD (Shrugging)", "Anonymous. Above my pay grade, lady."),
        ("SCENE", "EXT. CITY POLICE STATION – FRONT STEPS – DAWN"),
        ("CAM", "Wide shot; a black car idles at the curb."),
        ("ACT", "Theo leans against the car, holding two paper cups of coffee."),
        ("NADIA (Suspicious)", "IT contractors don't have two million dollars, Theo."),
        ("THEO (Sheepish)", "No. They don't."),
        ("NADIA (Angry)", "Who are you really? Tell me right now or I walk."),
        ("THEO (Hesitant)", "My name is Theo Kellan. Kellan, as in Arthur Kellan."),
        ("NADIA (Shocked)", "The chairman? You're the chairman's son?"),
        ("THEO (Earnest)", "He doesn't know I'm working there. I needed to see who was robbing him."),
        ("NADIA (Bitter)", "So I'm your bait."),
        ("THEO (Pleading)", "You're my witness. And the only person in that tower I trust."),
        ("SCENE", "INT. MERIDIAN CREST TOWER – CFO OFFICE – DAY"),
        ("CAM", "A wall of screens shows the gala video on every news channel."),
        ("ACT", "Selene throws a tablet onto Grant's desk; on it, a photo of Nadia getting into Theo's car."),
        ("SELENE (Furious)", "She's out. And she isn't alone."),
        ("GRANT (Icy)", "That's the contractor from the server floor. Who paid her bail?"),
        ("SELENE (Nervous)", "Nobody will say. The money came from a family trust."),
        ("CAM", "Close-up on Grant zooming into Theo's face."),
        ("GRANT (Menacing)", "Find out who that man is. Tonight. Before he finds out who we are."),
        ("SFX", "Ominous low drone. Cut to black."),
    ], {"crlf"}),
    Ep(5, "Nadia walks into the board meeting as Theo's consultant and accuses Grant to his face.", "", [
        ("SCENE", "INT. MERIDIAN CREST TOWER – BOARDROOM – DAY"),
        ("CAM", "A long glass table, twelve directors, the city skyline behind them."),
        ("GRANT (Confident)", "The pension shortfall is fully explained. The thief has been arrested."),
        ("SFX", "The double doors swing open."),
        ("ACT", "Nadia strides in wearing a tailored grey suit, Theo one step behind her."),
        ("CAM", "Grant's coffee cup stops halfway to his lips."),
        ("NADIA (Cold, Commanding)", "The thief is sitting at the head of this table."),
        ("GRANT (Outraged)", "Security! This woman is out on bail for fraud!"),
        ("ARTHUR (Commanding)", "Sit down, Grant. My son tells me she has something worth hearing."),
        ("GRANT (Stunned)", "Your... son?"),
        ("THEO (Calm)", "Hello, Grant. I fixed your printer last Tuesday."),
        ("ACT", "Nadia slides a folder of transfer records down the glass table to the chairman."),
        ("NADIA (Steady)", "Twelve transfers. Forty-eight million. All approved with Grant's code."),
        ("PRIYA (Sharp)", "Approval codes can be stolen. HR has a whole file on Miss Okonjo's debts."),
        ("NADIA (Defiant)", "My only debt is my mother's rent, Priya. And Grant just bought her building."),
        ("ACT", "A director gasps; murmurs ripple around the table."),
        ("GRANT (Smiling, Venomous)", "Anyone can print a spreadsheet, Arthur. Where is the original ledger?"),
        ("CAM", "Close-up on Nadia as she glances at Theo."),
        ("THEO (Quiet)", "The original is somewhere safe."),
        ("ARTHUR (Grave)", "Somewhere safe is not evidence."),
        ("SCENE", "INT. MERIDIAN CREST TOWER – BOARDROOM – CONTINUOUS"),
        ("ACT", "Arthur rises from his chair; the room falls silent."),
        ("ARTHUR (Cold)", "Miss Okonjo, you have accused my CFO in front of my entire board."),
        ("ARTHUR (Deadly)", "You have until Friday to put the real ledger on this table."),
        ("NADIA (Determined)", "And if I can't?"),
        ("ARTHUR (Chilling)", "Then I will press charges myself, and my son will never work in this city again."),
        ("SFX", "Ominous sting. Hold on Grant's slow smile."),
    ], {"no_continuity"}),
    Ep(6, "To reach the family vault that holds the ledger backup, Theo proposes a ninety-day contract engagement.",
       "Picking up at the Episode 5 cliffhanger: the chairman gives Nadia until Friday.", [
        ("SCENE", "INT. THEO'S PENTHOUSE – NIGHT"),
        ("CAM", "City lights through floor-to-ceiling windows."),
        ("ACT", "Nadia paces in front of the glass, the chairman's deadline circled on a wall calendar."),
        ("NADIA (Frustrated)", "The USB is in a sugar tin at my mother's, and Grant's men are watching her building."),
        ("THEO (Thoughtful)", "Then we use the other copy. My grandfather's vault keeps every board backup."),
        ("NADIA (Hopeful)", "So open it."),
        ("THEO (Wry)", "[laughs] Only family can open it. Spouses included."),
        ("ACT", "Theo sets a velvet ring box on the glass table between them."),
        ("CAM", "Close-up on a diamond ring catching the light."),
        ("NADIA (Shocked)", "Are you proposing to me? We met four days ago!"),
        ("THEO (Serious)", "A contract. Ninety days. You get the vault, I get the truth about my father's company."),
        ("NADIA (Wary)", "And when the ninety days are up?"),
        ("THEO (Soft)", "You walk away with your name back. No strings. [smiles]"),
        ("ACT", "Nadia stares at the ring, then slides it onto her finger."),
        ("NADIA (Resolute)", "Fine. But I'm writing the contract."),
        ("SCENE", "INT. SELENE'S CAR – NIGHT"),
        ("CAM", "Selene in the back seat, scrolling a gossip site."),
        ("SFX", "Rain drums on the car roof."),
        ("SELENE (Mocking)", "[laughs] The chairman's son just announced his engagement. Guess to whom."),
        ("GRANT (Cold)", "The thief and the heir. How touching."),
        ("SELENE (Gloating)", "She'll be untouchable once she's a Kellan. [chuckles]"),
        ("GRANT (Menacing)", "Not if she's arrested at her own engagement party."),
        ("ACT", "Grant opens a jewelry case; inside lies the Kellan sapphire necklace."),
        ("SELENE (Shocked)", "That's Arthur's late wife's necklace. Where did you get it?"),
        ("GRANT (Smirking)", "From the family vault, last year. Nobody has noticed it's missing."),
        ("CAM", "Close-up on the sapphire glinting in the dark."),
        ("GRANT (Chilling)", "Saturday, it turns up in the bride's handbag. In front of two hundred guests."),
        ("SELENE (Venomous)", "[laughs] Then we make sure the bride never reaches the altar."),
        ("SFX", "Ominous sting. The car pulls away into the dark."),
    ], {"inline_tags"}),
    Ep(7, "At the engagement party Selene accuses Nadia of stealing the Kellan sapphire, and security demands her bag.",
       "Picking up at the Episode 6 cliffhanger: Grant plans to plant the necklace at the party.", [
        ("SCENE", "EXT. KELLAN ESTATE – GARDEN TERRACE – NIGHT"),
        ("ACT", "Lanterns hang over a crowded terrace; Nadia, in emerald silk, stands beside Theo."),
        ("SFX", "A string quartet plays softly."),
        ("ARTHUR (Stiff)", "Welcome to the family, Miss Okonjo. For ninety days, apparently."),
        ("NADIA (Polite, Steely)", "Long enough, Mr. Kellan. Friday is still Friday."),
        ("ACT", "Selene glides past and hugs Nadia, her hand dipping into Nadia's handbag for one second."),
        ("THEO (Low)", "Did she just touch your bag?"),
        ("NADIA (Uneasy)", "She hugged me. Selene never hugs anyone."),
        ("ACT", "Selene taps a champagne glass with a spoon until the crowd falls silent."),
        ("SELENE (Sweet)", "A toast to the bride, who has such expensive taste."),
        ("SELENE (Accusing, Shouting)", "Mr. Kellan, your late wife's sapphire necklace is missing from the vault!"),
        ("ARTHUR (Shocked)", "What? Nobody has opened that vault in a year."),
        ("SELENE (Venomous)", "Nobody except the woman who's been asking about it all week."),
        ("ACT", "Every head on the terrace turns toward Nadia."),
        ("NADIA (Furious)", "You planted something on me. I felt your hand!"),
        ("GRANT (Smooth)", "Then she won't mind opening her bag. Innocent people never do."),
        ("ACT", "Two security guards step forward and block the stairs."),
        ("THEO (Shouting)", "Nobody touches her! She's my fiancée!"),
        ("ARTHUR (Cold)", "And this is my house. Open the bag, Miss Okonjo."),
        ("SCENE", "EXT. KELLAN ESTATE – GARDEN TERRACE – CONTINUOUS"),
        ("ACT", "Nadia's fingers tremble on the clasp of her emerald handbag."),
        ("NADIA (Whispering)", "Theo, if it's in there, I'm going straight back to that cell."),
        ("THEO (Whispering, Fierce)", "Then don't open it. Let them drag me in with you."),
        ("GRANT (Gloating)", "Tick tock, Miss Okonjo. Two hundred witnesses are waiting."),
        ("ACT", "The quartet stops mid-note; a guest raises a phone to record."),
        ("SFX", "The handbag clasp clicks open."),
        ("ACT", "A blue light glints from inside the bag."),
        ("SELENE (Gloating, Shrieking)", "There! I told you! Who's the thief now?"),
        ("SFX", "Ominous sting. Freeze on Nadia's face."),
    ]),
    Ep(8, "Nadia proves the necklace was planted, and HR director Priya agrees to talk, until a crash cuts her off.",
       "Picking up at the Episode 7 cliffhanger: the sapphire glints inside Nadia's handbag.", [
        ("SCENE", "EXT. KELLAN ESTATE – GARDEN TERRACE – NIGHT"),
        ("CAM", "Close-up on the sapphire necklace in Nadia's open bag."),
        ("ACT", "Nadia lifts the necklace out and holds it up for the crowd to see."),
        ("NADIA (Cold, Steady)", "Yes, it's in my bag. Now ask how it got there."),
        ("GRANT (The Smiling CFO) (Cold)", "Thieves always have a story. Call the police, Arthur."),
        ("NADIA (Commanding)", "Theo, the terrace cameras. Put them on the big screen."),
        ("ACT", "Theo taps his phone; the party slideshow switches to security footage."),
        ("CAM", "On screen, Selene's hand slips the necklace into Nadia's bag during the hug."),
        ("SFX", "The crowd gasps."),
        ("ARTHUR (Furious)", "Selene! In my own house!"),
        ("SELENE (Panicking)", "That footage is fake! Grant, tell them!"),
        ("GRANT (The Smiling CFO) (Smooth)", "I've never seen this woman before tonight."),
        ("ACT", "Selene stares at Grant, realizing he has just abandoned her."),
        ("SCENE", "INT. KELLAN ESTATE – SERVICE CORRIDOR – NIGHT"),
        ("CAM", "A narrow corridor lit by a single bulb."),
        ("ACT", "Priya Desai corners Nadia by the kitchen door, glancing over her shoulder."),
        ("PRIYA (Nervous, Whispering)", "If he can drop Selene that fast, he'll drop me next."),
        ("NADIA (Wary)", "Then help me before he does. What do you know?"),
        ("PRIYA (Fearful)", "Grant doesn't sign the transfers. He only collects them."),
        ("NADIA (Shocked)", "Then who signs them?"),
        ("THEO (O.S.) (Pleading)", "Nadia, where are you? We need to go, now!"),
        ("PRIYA (Hurried)", "Meet me at the Pier Street garage in an hour. I'll bring the originals."),
        ("SCENE", "EXT. PIER STREET – NIGHT"),
        ("CAM", "Rain on a deserted street; Priya hurries toward her car."),
        ("PRIYA (On phone) (Urgent)", "Nadia, I'm here. It isn't Grant who signs them. It's—"),
        ("SFX", "Headlights flare; tires screech; a violent crash of metal and glass."),
        ("NADIA (V.O.) (Screaming)", "Priya? Priya! Can you hear me? Who signs them?"),
        ("SFX", "Ominous swell. The line goes dead."),
    ]),
    Ep(9, "Priya lies in a coma, Grant attacks Nadia in a parking garage, and the transfer orders carry a shocking name.",
       "Picking up at the Episode 8 cliffhanger: Priya's car is hit mid-sentence.", [
        ("SCENE", "INT. ST. BRIGID HOSPITAL – ICU – NIGHT"),
        ("CAM", "Monitors beep over Priya's bandaged, unconscious face."),
        ("DOCTOR (Grave)", "She's in a coma. Whoever hit her didn't even brake."),
        ("NADIA (Guilty, Shaking)", "She was coming to meet me. This is my fault."),
        ("THEO (Firm)", "It's Grant's fault. And he just made a mistake."),
        ("ACT", "A nurse hands Nadia a muddy envelope recovered from Priya's car."),
        ("NURSE (Kind)", "The paramedics found this under her seat. It has your name on it."),
        ("CAM", "Close-up on the envelope: FOR NADIA, ORIGINALS."),
        ("SCENE", "INT. ST. BRIGID HOSPITAL – PARKING GARAGE – NIGHT"),
        ("CAM", "Flickering fluorescent tubes over rows of cars."),
        ("ACT", "Nadia walks alone toward her car, the envelope pressed to her chest."),
        ("SFX", "Footsteps echo behind her."),
        ("ACT", "Grant steps out from behind a pillar and shoves Nadia hard against a car door."),
        ("GRANT (Menacing)", "Give me the envelope and you might live to see Friday."),
        ("NADIA (Defiant, Gasping)", "Like Priya lived? You ran her off the road!"),
        ("GRANT (Cold)", "Priya drove badly. Accidents are contagious, Nadia."),
        ("ACT", "Grant grabs the envelope and wrenches it from her hands."),
        ("SFX", "A car horn blares as headlights flood the garage."),
        ("ACT", "Theo's car screeches to a stop; Theo leaps out and drags Grant off her."),
        ("THEO (Furious)", "Touch her again and I'll bury you myself!"),
        ("ACT", "Grant drops the envelope and backs away into the dark, smiling."),
        ("GRANT (Taunting)", "Open it, heir. You won't like whose name is inside."),
        ("SCENE", "INT. THEO'S CAR – NIGHT"),
        ("CAM", "The dome light is on; Nadia and Theo lean over the papers."),
        ("ACT", "Nadia flips to the last transfer order and freezes."),
        ("NADIA (Whispering, Horrified)", "Theo. Every transfer was signed by the same person."),
        ("THEO (Uneasy)", "Grant?"),
        ("NADIA (Trembling)", "Not Grant. Look at the signature. Do you recognize it?"),
        ("CAM", "Close-up on Theo's face draining of color."),
        ("THEO (Broken)", "That's my father's signature."),
        ("SFX", "Ominous sting. Cut to black."),
    ], {"colon_outside"}),
    Ep(10, "On Friday Nadia exposes the scheme before the shareholders and Grant is arrested, but the chairman has a plan.",
       "Picking up at the Episode 9 cliffhanger: the transfer orders carry Arthur Kellan's signature.", [
        ("SCENE", "INT. MERIDIAN CREST TOWER – AUDITORIUM – DAY"),
        ("CAM", "Three hundred shareholders fill an auditorium; Arthur stands at the podium."),
        ("ARTHUR (Commanding)", "It's Friday, Miss Okonjo. Show us your ledger, or leave in handcuffs."),
        ("ACT", "Nadia walks onto the stage and plugs the USB into the presentation laptop."),
        ("NADIA (Steady)", "Forty-eight million dollars, stolen from the people who built this company."),
        ("CAM", "The giant screen fills with transfer orders and Halcyon Bay bank statements."),
        ("NADIA (Cold, Accusing)", "Grant Voss moved the money. Selene March hid it. Priya Desai nearly died for knowing."),
        ("GRANT (Shouting)", "Lies! This is a forgery by a convicted thief!"),
        ("ACT", "Selene stands up in the third row, mascara streaked, holding up her phone."),
        ("SELENE (Defiant)", "It's real. I recorded every call he made. Including the one about Priya's car."),
        ("GRANT (Enraged)", "You treacherous little—"),
        ("SFX", "Doors bang open; police officers file down the aisle."),
        ("OFFICER (Formal)", "Grant Voss, you are under arrest for fraud and attempted murder."),
        ("ACT", "Officers twist Grant's arms behind his back and march him out as the crowd films."),
        ("GRANT (Shouting, Furious)", "You think I built this alone? Ask the chairman who signed the orders!"),
        ("SCENE", "INT. MERIDIAN CREST TOWER – CHAIRMAN'S OFFICE – DAY"),
        ("CAM", "Arthur pours two glasses of whisky by the window."),
        ("THEO (Furious)", "You signed every transfer, Dad. Your own pension fund."),
        ("ARTHUR (Calm)", "I signed what Grant put in front of me. Now Grant is gone, and the board loves her."),
        ("ACT", "Arthur turns to Nadia and holds out a glass."),
        ("ARTHUR (Smiling, Chilling)", "I need a new CFO. One the shareholders trust."),
        ("NADIA (Wary)", "And if I say no?"),
        ("ARTHUR (Menacing)", "Then the sugar tin at your mother's apartment becomes very interesting to the police."),
        ("CAM", "Close-up on Nadia; the color drains from her face."),
        ("NADIA (Whispering)", "How do you know about the sugar tin?"),
        ("ARTHUR (Deadly calm)", "Welcome to the family business, Nadia. You'll never leave it."),
        ("SFX", "Ominous sting. Cut to black."),
    ], {"bom"}),
]

EMBER_THRONE = [
    Ep(1, "Healer Asha is accused of poisoning the young queen at her own coronation.", "Opening episode.", [
        ("SCENE", "INT. EMBER CITADEL – THRONE HALL – DAY"),
        ("CAM", "Wide shot of crimson banners and a crowd of nobles."),
        ("ACT", "Queen Maren collapses on the throne steps, her goblet spilling dark wine."),
        ("CORVIN (Shouting)", "The queen is poisoned! Seize the healer!"),
        ("ASHA (Horrified)", "I only mixed her a tonic for her headache!"),
        ("ACT", "Guards grab Asha and force her to her knees before the throne."),
        ("CORVIN (Cold, Menacing)", "You will hang at dawn, witch."),
        ("SFX", "The crowd roars."),
        ("ASHA (Desperate)", "Let me treat her! She'll die before dawn without me!"),
        ("MAREN (Weak, Whispering)", "Corvin... the cup... you gave me the cup."),
        ("CORVIN (Deadly)", "Take the healer to the tower. Now."),
        ("SFX", "Ominous sting."),
    ]),
    Ep(2, "In the tower, Asha learns the regent will trade the antidote for her confession.",
       "Picking up at the Episode 1 cliffhanger: Asha is dragged to the tower.", [
        ("SCENE", "INT. EMBER CITADEL – TOWER CELL – NIGHT"),
        ("CAM", "Moonlight through a slit window."),
        ("ACT", "Asha paces the cell, grinding herbs from her sleeve on the stone floor."),
        ("GUARD (Gruff)", "Save your herbs, healer. The gallows don't need medicine."),
        ("ASHA (Pleading)", "The queen is dying. One antidote, that's all I ask."),
        ("SFX", "A key turns in the lock."),
        ("ACT", "Regent Corvin enters alone, holding a small black vial."),
        ("CORVIN (Smooth)", "Confess to the poisoning, and the queen gets your antidote."),
        ("ASHA (Defiant)", "You poisoned her yourself. She said so!"),
        ("CORVIN (Cruel)", "A dying girl's ramblings. Who will believe a witch?"),
        ("ACT", "Corvin tips the black vial over the candle flame; it hisses."),
        ("CORVIN (Chilling)", "Choose before the candle burns out. Her life or yours?"),
        ("SFX", "Ominous swell."),
    ]),
]


# ---------------------------------------------------------------- timing
def time_blocks(ep: Ep) -> tuple[list[Block], list[tuple[str, int, int]]]:
    """Lay beats on a clock (centiseconds). Camera and sound overlap what follows, like the real format."""
    t, scene, blocks, scenes = 0, 0, [], []
    for label, text in ep.beats:
        words = len(text.split())
        if label == "SCENE":
            scene += 1
            scenes.append((text, t, t))
            continue
        if label == "CAM":
            blocks.append(Block("CAMERA", t, t + 240, text, scene))
        elif label == "SFX":
            blocks.append(Block("SOUND FX", t, t + 200, text, scene))
        elif label == "ACT":
            d = 160 + 20 * words
            blocks.append(Block("ACTION", t, t + d, text, scene))
            t += d + 30
        else:
            d = 80 + 34 * words
            blocks.append(Block(label, t, t + d, text, scene))
            t += d + 40
        head, s0, _ = scenes[-1]
        scenes[-1] = (head, s0, max(b.t1 for b in blocks if b.scene == scene))
    return blocks, scenes


def ts(cs: int, hhmmss: bool = False) -> str:
    m, s = divmod(cs, 6000)
    if hhmmss:
        return f"{m // 60:02d}:{m % 60:02d}:{s / 100:05.2f}"
    return f"{m:02d}:{s / 100:05.2f}"


def speaker(label: str) -> str:
    return label.split("(")[0].strip()


# ---------------------------------------------------------------- writers
def kuku(ep: Ep) -> str:
    st = ep.style
    hh = "hhmmss" in st
    blocks, scenes = time_blocks(ep)
    out = [f"EPISODE NUMBER: Episode {ep.num}", f"EPISODE LOGLINE: {ep.logline}"]
    if "no_continuity" not in st:
        out.append(f"CONTINUITY NOTES: {ep.continuity}")
    out += ["", "--- SCRIPT START ---", ""]
    f_head, f_start, f_end = (("SCENE HEADING", "SCENE START", "SCENE END") if "upper_fields" in st
                              else ("Scene heading", "Scene start", "Scene end"))
    for n, (head, s0, s1) in enumerate(scenes, 1):
        out += [f"SCENE {n:02d}", f"{f_head}: {head}", f"{f_start}: {ts(s0, hh)}", f"{f_end}: {ts(s1, hh)}", "",
                "ACTIONS AND DIALOGUE:" if "upper_fields" in st else "Actions and dialogue:", ""]
        for b in (b for b in blocks if b.scene == n):
            body = b.text if b.label in ("CAMERA", "ACTION", "SOUND FX") else f'"{b.text}"'
            label = f"**{b.label}**:" if "colon_outside" in st else f"**{b.label}:**"
            gap = [""] if "colon_outside" in st else []
            out += [label, *gap, f"Start: ({ts(b.t0, hh)}) | End: ({ts(b.t1, hh)})", body, ""]
    out += ["--- SCRIPT END ---", ""]
    return "\n".join(out)


def srt(ep: Ep) -> str:
    """SDH-style subtitles: 'NAME: line' cues plus '[sound]' cues."""
    def t(cs: int) -> str:
        return ts(cs, True).replace(".", ",") + "0"
    cues = [b for b in time_blocks(ep)[0] if b.label not in ("CAMERA", "ACTION")]
    out = []
    for n, b in enumerate(cues, 1):
        text = f"[{b.text}]" if b.label == "SOUND FX" else f"{speaker(b.label)}: {b.text}"
        out += [str(n), f"{t(b.t0)} --> {t(b.t1)}", text, ""]
    return "\n".join(out)


def vtt(ep: Ep) -> str:
    """WebVTT with <v Speaker> voice tags, a NOTE block and cue settings."""
    out = ["WEBVTT", "", "NOTE Generated fixture: dialogue only.", ""]
    for b in time_blocks(ep)[0]:
        if b.label in ("CAMERA", "ACTION", "SOUND FX"):
            continue
        out += [f"{ts(b.t0, True)}0 --> {ts(b.t1, True)}0 line:85%", f"<v {speaker(b.label).title()}>{b.text}", ""]
    return "\n".join(out)


def transcript(ep: Ep) -> str:
    """'[HH:MM:SS] Name: line', whole seconds, like an auto-transcript export."""
    out = [f"Transcript - Glass Ledger, episode {ep.num}", ""]
    for b in time_blocks(ep)[0]:
        if b.label in ("CAMERA", "ACTION", "SOUND FX"):
            continue
        out.append(f"[{ts(b.t0 // 100 * 100, True)[:8]}] {speaker(b.label).title()}: {b.text}")
    return "\n".join(out) + "\n"


def write(path: Path, text: str, style: set[str] | frozenset[str] = frozenset()) -> None:
    data = text.replace("\n", "\r\n") if "crlf" in style else text
    path.write_bytes(data.encode("utf-8-sig" if "bom" in style else "utf-8"))


def reset(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.iterdir():
        if old.suffix in (".txt", ".srt", ".vtt"):
            old.unlink()
    return folder


def main() -> None:
    show = reset(ROOT / "new_show")
    for ep in GLASS_LEDGER:
        blocks, _ = time_blocks(ep)
        dur = max(b.t1 for b in blocks) / 100
        assert 60 <= dur <= 120, f"new_show ep {ep.num}: {dur:.1f}s"
        assert 25 <= len(blocks) <= 45, f"new_show ep {ep.num}: {len(blocks)} blocks"
        write(show / f"episode-{ep.num:03d}-Episode {7302000 + 7919 * ep.num}.txt", kuku(ep), ep.style)
        print(f"new_show  E{ep.num:02d} {dur:6.1f}s {len(blocks):3d} blocks  {', '.join(sorted(ep.style)) or '-'}")
    mini = reset(ROOT / "mini_show")
    for ep in EMBER_THRONE:
        write(mini / f"episode-{ep.num:03d}-Episode {5100000 + 313 * ep.num}.txt", kuku(ep))
    subs = reset(ROOT / "subs_show")
    write(subs / "episode-001.srt", srt(GLASS_LEDGER[0]))
    write(subs / "episode-002.vtt", vtt(GLASS_LEDGER[1]))
    write(subs / "episode-003-transcript.txt", transcript(GLASS_LEDGER[2]))
    print(f"wrote {ROOT}/new_show, mini_show, subs_show")


if __name__ == "__main__":
    main()
