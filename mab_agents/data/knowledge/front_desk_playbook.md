# Front Desk Playbook (starter)

This is a starting template written for a collision shop front desk. Edit it to
match how Mullikin's actually works; the agents treat whatever is in this folder
as shop policy and will quote it to staff.

## The job in one paragraph

The front desk owns every customer touch that is not a wrench: the phone, the
check-in, the daily status texts, the insurer and rental phone calls, the
delivery and the payment. The goal is that no customer ever has to call us to
find out what is happening with their car.

## Daily routine

**Opening (first 30 minutes)**
1. Open the Today screen in MAB (`mab serve`) or run `mab brief`.
2. Work the "Needs an update" list first: every customer who has not heard from us in 2 days gets a text today.
3. Check "Ready for pickup": confirm pickup times and tell customers what they owe.
4. Check "Awaiting approval": call or portal-message the adjuster on anything older than 24 hours.
5. Confirm today's drop-offs and that a rental is arranged where the policy covers one.

**Through the day**
- Answer the phone by the second ring: "Thanks for calling Mullikin's Auto Body, this is <name>, how can I help?"
- Every stage change from the floor becomes a customer text the same day. Draft it in MAB, read it once, send it.
- Log every call or text on the RO (notes) so the next person can see it.

**Closing**
- Any car promised for tomorrow: confirm with production that it is on track, text the customer either way.
- Lock keys in the key box, mark which keys belong to cars that are ready.
- Deposit / batch out the card terminal.

## Phone scripts

### New claim / first call
Ask, in this order: name and best mobile number; year, make, model; what happened and where the damage is; is it drivable (any warning lights, leaks, wheel damage, airbag deployment means NO); insurance company and claim number, or customer pay; does the policy have rental coverage; when can they bring it in.

Say: "You choose who repairs your car. If you tell the insurance company you are bringing it to Mullikin's, we handle the paperwork with them, including the estimate and any additional damage we find." Do not quote the law or say "in every state".

Do not promise a completion date on the phone. Say: "Once we take it apart and the insurance company approves the repair plan, we will give you a target date and text you at every step."

### Status call
Look the customer up by phone number in MAB. Read the current stage in plain words (the status text draft is already in plain words). If the car is waiting on the insurance company or a part, say so and say what we are doing about it. Then ask: "Would you like me to text you updates as it moves? We do that at every stage."

### Angry about a delay
Let them finish. Then: "You're right, that is longer than we want too. Here is exactly where it is today and the one thing it is waiting on. I am going to <action> and text you by <time>." Never blame the technician, the insurer or the parts vendor by name. If they want the manager, get the manager; do not say the manager is busy.

### "Why do I pay the deductible to you?"
"The insurance company subtracts your deductible from what they pay us, so it is collected by the shop at pickup. We take card, cash or check. If you believe the other driver was at fault, your insurance company may recover it for you later; that is a question for your adjuster."

### Insurance company calling
Get the claim number first, then the RO. Adjusters want: current status, whether the supplement is submitted, the promised date, and the rental return date. Never argue labor rates or operations on the phone; that is the estimator's or manager's conversation. Say: "Let me get <estimator> for you."

### Tow truck arrives
Get the tow bill and the driver's company name, photograph the car on the truck and after it is unloaded (all four corners), check for personal items and the key, write the RO as a tow-in, text the customer that it arrived.

### Rental
If the policy has rental: the carrier sets up the reservation; we give the rental company the promised date and update it when it moves. If the car will be late, call the adjuster for an extension before the rental runs out, not after. Customer pay with no coverage: offer the rental company's shop rate if we have one.

### After-hours pickup
Only if the balance is paid and a release is signed. Keys go in the lockbox; the code goes to the customer's mobile number on the RO only.

### "The insurer says I have to use their shop"
"You choose who repairs your car. The insurance company can recommend shops, but it can't require one. They pay based on the estimate they approve, and if what they approve is less than our invoice we tell you before work starts, not at pickup. We work with <carrier> regularly." Then move on to scheduling. If they push, offer the manager. Never say "the law says" or promise the insurer will pay our full invoice.

## Check-in checklist

1. Photo ID and insurance card; confirm phone number and preferred contact (text or call).
2. Signed repair authorization and direction-to-pay (we bill the insurer directly).
3. Walk-around with the customer: photograph all four corners, VIN, odometer, dash lights, interior, and any prior damage; point prior damage out and note it on the RO.
4. Personal items out; toll transponder, garage opener and child seats noted.
5. Fuel level and mileage on the RO.
6. Explain the sequence: teardown, insurer approval of any additional damage, parts, body, paint, reassembly, scan and calibration, quality check.
7. Set expectations on updates: "You will get a text at every stage."
8. Rental handed off or confirmed.

## Delivery checklist

1. Car washed, interior vacuumed, no dust in jambs, no tape, no overspray on glass or trim.
2. Final invoice matches the approved estimate plus supplements; deductible and any customer-pay items itemized.
3. Walk-around with the customer at the repaired area and panel gaps; show the calibration report and post-repair scan if the job had them.
4. Warranty wording handed over and explained in one sentence, exactly as written in the shop profile (shop.toml `warranty`); do not improvise warranty terms.
5. Payment collected: card, cash or check. Insurer checks made out to the customer must be endorsed.
6. Keys, personal items, rental return reminder.
7. Review request text goes out the same day.

## Payment policy (template, edit to match the shop)

- Deductible is due at pickup. We do not "waive" deductibles. Anything the insurer declines (betterment on tires or batteries, operations they denied, upgrades the customer asked for) is told to the customer before the work is done and shows on the RO as a customer-pay item, never as a surprise at the counter.
- Customer-pay repairs: 50% deposit on parts over $500 before ordering; balance at pickup.
- Storage accrues daily on vehicles left more than 3 days after they are ready, and on total losses awaiting carrier pickup after the carrier is notified.
- Insurer-issued checks payable to the customer must be endorsed at pickup.

## Escalate to the manager when

- A customer asks to speak to the manager.
- An adjuster denies a safety-related operation (scan, calibration, pretensioner) or asks the shop to skip an OEM procedure.
- A total-loss conversation starts.
- Any threat of a complaint to the insurance commissioner, the BBB or a lawyer.
- Any injury, fluid spill, or damage to a customer car in our care.
