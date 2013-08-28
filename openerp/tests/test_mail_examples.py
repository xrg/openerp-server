#!/usr/bin/env python
# -*- coding: utf-8 -*-

MISC_HTML_SOURCE = """
<font size="2" style="color: rgb(31, 31, 31); font-family: monospace; font-variant: normal; line-height: normal; ">test1</font>
<div style="color: rgb(31, 31, 31); font-family: monospace; font-variant: normal; line-height: normal; font-size: 12px; font-style: normal; ">
<b>test2</b></div><div style="color: rgb(31, 31, 31); font-family: monospace; font-variant: normal; line-height: normal; font-size: 12px; ">
<i>test3</i></div><div style="color: rgb(31, 31, 31); font-family: monospace; font-variant: normal; line-height: normal; font-size: 12px; ">
<u>test4</u></div><div style="color: rgb(31, 31, 31); font-family: monospace; font-variant: normal; line-height: normal; font-size: 12px; ">
<strike>test5</strike></div><div style="color: rgb(31, 31, 31); font-family: monospace; font-variant: normal; line-height: normal; ">
<font size="5">test6</font></div><div><ul><li><font color="#1f1f1f" face="monospace" size="2">test7</font></li><li>
<font color="#1f1f1f" face="monospace" size="2">test8</font></li></ul><div><ol><li><font color="#1f1f1f" face="monospace" size="2">test9</font>
</li><li><font color="#1f1f1f" face="monospace" size="2">test10</font></li></ol></div></div>
<blockquote style="margin: 0 0 0 40px; border: none; padding: 0px;"><div><div><div><font color="#1f1f1f" face="monospace" size="2">
test11</font></div></div></div></blockquote><blockquote style="margin: 0 0 0 40px; border: none; padding: 0px;">
<blockquote style="margin: 0 0 0 40px; border: none; padding: 0px;"><div><font color="#1f1f1f" face="monospace" size="2">
test12</font></div><div><font color="#1f1f1f" face="monospace" size="2"><br></font></div></blockquote></blockquote>
<font color="#1f1f1f" face="monospace" size="2"><a href="http://google.com">google</a></font>
<a href="javascript:alert('malicious code')">test link</a>
"""

EDI_LIKE_HTML_SOURCE = """<div style="font-family: 'Lucica Grande', Ubuntu, Arial, Verdana, sans-serif; font-size: 12px; color: rgb(34, 34, 34); background-color: #FFF; ">
    <p>Hello ${object.partner_id.name},</p>
    <p>A new invoice is available for you: </p>
    <p style="border-left: 1px solid #8e0000; margin-left: 30px;">
       &nbsp;&nbsp;<strong>REFERENCES</strong><br />
       &nbsp;&nbsp;Invoice number: <strong>${object.number}</strong><br />
       &nbsp;&nbsp;Invoice total: <strong>${object.amount_total} ${object.currency_id.name}</strong><br />
       &nbsp;&nbsp;Invoice date: ${object.date_invoice}<br />
       &nbsp;&nbsp;Order reference: ${object.origin}<br />
       &nbsp;&nbsp;Your contact: <a href="mailto:${object.user_id.email or ''}?subject=Invoice%20${object.number}">${object.user_id.name}</a>
    </p>
    <br/>
    <p>It is also possible to directly pay with Paypal:</p>
    <a style="margin-left: 120px;" href="${object.paypal_url}">
        <img class="oe_edi_paypal_button" src="https://www.paypal.com/en_US/i/btn/btn_paynowCC_LG.gif"/>
    </a>
    <br/>
    <p>If you have any question, do not hesitate to contact us.</p>
    <p>Thank you for choosing ${object.company_id.name or 'us'}!</p>
    <br/>
    <br/>
    <div style="width: 375px; margin: 0px; padding: 0px; background-color: #8E0000; border-top-left-radius: 5px 5px; border-top-right-radius: 5px 5px; background-repeat: repeat no-repeat;">
        <h3 style="margin: 0px; padding: 2px 14px; font-size: 12px; color: #DDD;">
            <strong style="text-transform:uppercase;">${object.company_id.name}</strong></h3>
    </div>
    <div style="width: 347px; margin: 0px; padding: 5px 14px; line-height: 16px; background-color: #F2F2F2;">
        <span style="color: #222; margin-bottom: 5px; display: block; ">
        ${object.company_id.street}<br/>
        ${object.company_id.street2}<br/>
        ${object.company_id.zip} ${object.company_id.city}<br/>
        ${object.company_id.state_id and ('%s, ' % object.company_id.state_id.name) or ''} ${object.company_id.country_id.name or ''}<br/>
        </span>
        <div style="margin-top: 0px; margin-right: 0px; margin-bottom: 0px; margin-left: 0px; padding-top: 0px; padding-right: 0px; padding-bottom: 0px; padding-left: 0px; ">
            Phone:&nbsp; ${object.company_id.phone}
        </div>
        <div>
            Web :&nbsp;<a href="${object.company_id.website}">${object.company_id.website}</a>
        </div>
    </div>
</div></body></html>"""

TEXT_1 = """I contact you about our meeting tomorrow. Here is the schedule I propose:
9 AM: brainstorming about our new amazing business app
9.45 AM: summary
10 AM: meeting with Ignasse to present our app
Is everything ok for you ?
--
MySignature"""

TEXT_1_IN = ["""I contact you about our meeting tomorrow. Here is the schedule I propose:
9 AM: brainstorming about our new amazing business app
9.45 AM: summary
10 AM: meeting with Ignasse to present our app
Is everything ok for you ?"""]
TEXT_1_OUT = ["""--
MySignature"""]

TEXT_2 = """Salut Raoul!
Le 28 oct. 2012 à 00:02, Raoul Grosbedon a écrit :

> I contact you about our meeting tomorrow. Here is the schedule I propose: (quote)

Of course. This seems viable.

> 2012/10/27 Bert Tartopoils :
>> blahblahblah (quote)?
>> 
>> blahblahblah (quote)
>> 
>> Bert TARTOPOILS
>> bert.tartopoils@miam.miam
>> 
> 
> 
> -- 
> RaoulSignature

Bert TARTOPOILS
bert.tartopoils@miam.miam
"""

TEXT_2_IN = ["Salut Raoul!", "Of course. This seems viable."]
TEXT_2_OUT = ["I contact you about our meeting tomorrow. Here is the schedule I propose: (quote)",
    """> 2012/10/27 Bert Tartopoils :
>> blahblahblah (quote)?
>> 
>> blahblahblah (quote)
>> 
>> Bert TARTOPOILS
>> bert.tartopoils@miam.miam
>> 
> 
> 
> -- 
> RaoulSignature"""]

HTML_1 = """<p>I contact you about our meeting for tomorrow. Here is the schedule I propose: (keep)
9 AM: brainstorming about our new amazing business app
9.45 AM: summary
10 AM: meeting with Ignasse to present our app
Is everything ok for you ?
--
MySignature</p>"""

HTML_1_IN = ["""I contact you about our meeting for tomorrow. Here is the schedule I propose: (keep)
9 AM: brainstorming about our new amazing business app
9.45 AM: summary
10 AM: meeting with Ignasse to present our app
Is everything ok for you ?"""]
HTML_1_OUT = ["""--
MySignature"""]

HTML_2 = """<div>
    <font><span>I contact you about our meeting for tomorrow. Here is the schedule I propose:</span></font>
</div>
<div>
    <ul>
        <li><span>9 AM: brainstorming about our new amazing business app</span></li>
        <li><span>9.45 AM: summary</span></li>
        <li><span>10 AM: meeting with Fabien to present our app</span></li>
    </ul>
</div>
<div>
    <font><span>Is everything ok for you ?</span></font>
</div>"""

HTML_2_IN = ["<font><span>I contact you about our meeting for tomorrow. Here is the schedule I propose:</span></font>",
    "<li><span>9 AM: brainstorming about our new amazing business app</span></li>",
    "<li><span>9.45 AM: summary</span></li>",
    "<li><span>10 AM: meeting with Fabien to present our app</span></li>",
    "<font><span>Is everything ok for you ?</span></font>"]
HTML_2_OUT = []

HTML_3 = """<div><pre>This is an answer.

Regards,
XXXXXX
----- Mail original -----</pre>


<pre>Hi, 


My CRM-related question.

Regards, 

XXXX</pre></div>"""

HTML_3_IN = ["""<div><pre>This is an answer.

Regards,
XXXXXX
----- Mail original -----</pre>"""]
HTML_3_OUT = ["Hi,", "My CRM-related question.",
    "Regards,"]

HTML_4 = """
<div>
    <div>Hi Nicholas,</div>
    <br>
    <div>I'm free now. 00447710085916.</div>
    <br>
    <div>Regards,</div>
    <div>Nicholas</div>
    <br>
    <span id="OLK_SRC_BODY_SECTION">
        <div style="font-family:Calibri; font-size:11pt; text-align:left; color:black; BORDER-BOTTOM: medium none; BORDER-LEFT: medium none; PADDING-BOTTOM: 0in; PADDING-LEFT: 0in; PADDING-RIGHT: 0in; BORDER-TOP: #b5c4df 1pt solid; BORDER-RIGHT: medium none; PADDING-TOP: 3pt">
            <span style="font-weight:bold">From: </span>OpenERP Enterprise &lt;<a href="mailto:sales@openerp.com">sales@openerp.com</a>&gt;<br><span style="font-weight:bold">Reply-To: </span>&lt;<a href="mailto:sales@openerp.com">sales@openerp.com</a>&gt;<br><span style="font-weight:bold">Date: </span>Wed, 17 Apr 2013 13:30:47 +0000<br><span style="font-weight:bold">To: </span>Microsoft Office User &lt;<a href="mailto:n.saxlund@babydino.com">n.saxlund@babydino.com</a>&gt;<br><span style="font-weight:bold">Subject: </span>Re: your OpenERP.com registration<br>
        </div>
        <br>
        <div>
            <p>Hello Nicholas Saxlund, </p>
            <p>I noticed you recently registered to our OpenERP Online solution. </p>
            <p>You indicated that you wish to use OpenERP in your own company. We would like to know more about your your business needs and requirements, and see how we can help you. When would you be available to discuss your project ?
            </p>
            <p>Best regards, </p>
            <pre><a href="http://openerp.com">http://openerp.com</a>
Belgium: +32.81.81.37.00
U.S.: +1 (650) 307-6736
India: +91 (79) 40 500 100
                        </pre>
        </div>
    </span>
</div>"""

HTML_5 = """<div><pre>Hi,

I have downloaded OpenERP installer 7.0 and successfully installed the postgresql server and the OpenERP.
I created a database and started to install module by log in as administrator.
However, I was not able to install any module due to "OpenERP Server Error" as shown in the attachement.
Could you please let me know how could I fix this problem?

&nbsp;Regards,
Goh Sin Yih


________________________________
 From: OpenERP Enterprise &lt;sales@openerp.com&gt;
To: sinyih_goh@yahoo.com 
Sent: Friday, February 8, 2013 12:46 AM
Subject: Feedback From Your OpenERP Trial
 

Hello Goh Sin Yih, 
Thank you for having tested OpenERP Online. 
I noticed you started a trial of OpenERP Online (gsy) but you did not decide to keep using it. 
So, I just wanted to get in touch with you to get your feedback. Can you tell me what kind of application you were you looking for and why you didn't decide to continue with OpenERP? 
Thanks in advance for providing your feedback, 
Do not hesitate to contact me if you have any questions, 
Thanks, 
</pre>"""

GMAIL_1 = """Hello,<div><br></div><div>Ok for me. I am replying directly in gmail, without signature.</div><div><br></div><div>Kind regards,</div><div><br></div><div>Demo.<br><br><div>On Thu, Nov 8, 2012 at 5:29 PM,  <span>&lt;<a href="mailto:dummy@example.com">dummy@example.com</a>&gt;</span> wrote:<br><blockquote><div>I contact you about our meeting for tomorrow. Here is the schedule I propose:</div><div><ul><li>9 AM: brainstorming about our new amazing business app&lt;/span&gt;&lt;/li&gt;</li>
<li>9.45 AM: summary</li><li>10 AM: meeting with Fabien to present our app</li></ul></div><div>Is everything ok for you ?</div>
<div><p>--<br>Administrator</p></div>

<div><p>Log in our portal at: <a href="http://localhost:8069#action=login&amp;db=mail_1&amp;login=demo">http://localhost:8069#action=login&amp;db=mail_1&amp;login=demo</a></p></div>
</blockquote></div><br></div>"""

GMAIL_1_IN = ['Ok for me. I am replying directly in gmail, without signature.']
GMAIL_1_OUT = ['Administrator', 'Log in our portal at:']

THUNDERBIRD_1 = """<div>On 11/08/2012 05:29 PM,
      <a href="mailto:dummy@example.com">dummy@example.com</a> wrote:<br></div>
    <blockquote>
      <div>I contact you about our meeting for tomorrow. Here is the
        schedule I propose:</div>
      <div>
        <ul><li>9 AM: brainstorming about our new amazing business
            app&lt;/span&gt;&lt;/li&gt;</li>
          <li>9.45 AM: summary</li>
          <li>10 AM: meeting with Fabien to present our app</li>
        </ul></div>
      <div>Is everything ok for you ?</div>
      <div>
        <p>--<br>
          Administrator</p>
      </div>
      <div>
        <p>Log in our portal at:
<a href="http://localhost:8069#action=login&amp;db=mail_1&amp;token=rHdWcUART5PhEnJRaXjH">http://localhost:8069#action=login&amp;db=mail_1&amp;token=rHdWcUART5PhEnJRaXjH</a></p>
      </div>
    </blockquote>
    Ok for me. I am replying directly below your mail, using Thunderbird, with a signature.<br><br>
    Did you receive my email about my new laptop, by the way ?<br><br>
    Raoul.<br><pre>-- 
Raoul Grosbedonn&#233;e
</pre>"""

THUNDERBIRD_1_IN = ['Ok for me. I am replying directly below your mail, using Thunderbird, with a signature.']
THUNDERBIRD_1_OUT = ['I contact you about our meeting for tomorrow.', 'Raoul Grosbedon']

HOTMAIL_1 = """<div>
    <div dir="ltr"><br>&nbsp;
        I have an amazing company, i'm learning OpenERP, it is a small company yet, but plannig to grow up quickly.
        <br>&nbsp;<br>Kindest regards,<br>xxx<br>
        <div>
            <div id="SkyDrivePlaceholder">
            </div>
            <hr id="stopSpelling">
            Subject: Re: your OpenERP.com registration<br>From: xxx@xxx.xxx<br>To: xxx@xxx.xxx<br>Date: Wed, 27 Mar 2013 17:12:12 +0000
            <br><br>
            Hello xxx,
            <br>
            I noticed you recently created an OpenERP.com account to access OpenERP Apps.
            <br>
            You indicated that you wish to use OpenERP in your own company.
            We would like to know more about your your business needs and requirements, and see how
            we can help you. When would you be available to discuss your project ?<br>
            Best regards,<br>
            <pre>
                <a href="http://openerp.com" target="_blank">http://openerp.com</a>
                Belgium: +32.81.81.37.00
                U.S.: +1 (650) 307-6736
                India: +91 (79) 40 500 100
            </pre>
        </div>
    </div>
</div>"""

HOTMAIL_1_IN = ["I have an amazing company, i'm learning OpenERP, it is a small company yet, but plannig to grow up quickly."]
HOTMAIL_1_OUT = ["Subject: Re: your OpenERP.com registration", " I noticed you recently created an OpenERP.com account to access OpenERP Apps.",
    "We would like to know more about your your business needs and requirements", "Belgium: +32.81.81.37.00"]

MSOFFICE_1 = """
<div>
<div class="WordSection1">
        <p class="MsoNormal">
            <span style="font-size:11.0pt;font-family:&quot;Calibri&quot;,&quot;sans-serif&quot;;color:#1F497D">
                Our requirements are simple. Just looking to replace some spreadsheets for tracking quotes and possibly using the timecard module.
                We are a company of 25 engineers providing product design services to clients.
            </span>
        </p>
        <p></p>
        <p></p>
        <p class="MsoNormal">
            <span style="font-size:11.0pt;font-family:&quot;Calibri&quot;,&quot;sans-serif&quot;;color:#1F497D">
                I’ll install on a windows server and run a very limited trial to see how it works.
                If we adopt OpenERP we will probably move to Linux or look for a hosted SaaS option.
            </span>
        </p>
        <p></p>
        <p></p>
        <p class="MsoNormal">
            <span style="font-size:11.0pt;font-family:&quot;Calibri&quot;,&quot;sans-serif&quot;;color:#1F497D">
                <br>
                I am also evaluating Adempiere and maybe others.
            </span>
        </p>
        <p></p>
        <p></p>
        <p class="MsoNormal">
            <span style="font-size:11.0pt;font-family:&quot;Calibri&quot;,&quot;sans-serif&quot;;color:#1F497D">
            </span>
        </p>
        <p>&nbsp;</p>
        <p></p>
        <p class="MsoNormal">
            <span style="font-size:11.0pt;font-family:&quot;Calibri&quot;,&quot;sans-serif&quot;;color:#1F497D">
                I expect the trial will take 2-3 months as this is not a high priority for us.
            </span>
        </p>
        <p></p>
        <p></p>
        <p class="MsoNormal">
            <span style="font-size:11.0pt;font-family:&quot;Calibri&quot;,&quot;sans-serif&quot;;color:#1F497D">
            </span>
        </p>
        <p>&nbsp;</p>
        <p></p>
        <p class="MsoNormal">
            <span style="font-size:11.0pt;font-family:&quot;Calibri&quot;,&quot;sans-serif&quot;;color:#1F497D">
                Alan
            </span>
        </p>
        <p></p>
        <p></p>
        <p class="MsoNormal">
            <span style="font-size:11.0pt;font-family:&quot;Calibri&quot;,&quot;sans-serif&quot;;color:#1F497D">
            </span>
        </p>
        <p>&nbsp;</p>
        <p></p>
        <div>
            <div style="border:none;border-top:solid #B5C4DF 1.0pt;padding:3.0pt 0in 0in 0in">
                <p class="MsoNormal">
                    <b><span style="font-size:10.0pt;font-family:&quot;Tahoma&quot;,&quot;sans-serif&quot;">
                        From:
                    </span></b>
                    <span style="font-size:10.0pt;font-family:&quot;Tahoma&quot;,&quot;sans-serif&quot;">
                        OpenERP Enterprise [mailto:sales@openerp.com]
                        <br><b>Sent:</b> Monday, 11 March, 2013 14:47<br><b>To:</b> Alan Widmer<br><b>Subject:</b> Re: your OpenERP.com registration
                    </span>
                </p>
                <p></p>
                <p></p>
            </div>
        </div>
        <p class="MsoNormal"></p>
        <p>&nbsp;</p>
        <p>Hello Alan Widmer, </p>
        <p></p>
        <p>I noticed you recently downloaded OpenERP. </p>
        <p></p>
        <p>
            Uou mentioned you wish to use OpenERP in your own company. Please let me more about your
            business needs and requirements? When will you be available to discuss about your project?
        </p>
        <p></p>
        <p>Thanks for your interest in OpenERP, </p>
        <p></p>
        <p>Feel free to contact me if you have any questions, </p>
        <p></p>
        <p>Looking forward to hear from you soon. </p>
        <p></p>
        <pre><p>&nbsp;</p></pre>
        <pre>--<p></p></pre>
        <pre>Nicolas<p></p></pre>
        <pre><a href="http://openerp.com">http://openerp.com</a><p></p></pre>
        <pre>Belgium: +32.81.81.37.00<p></p></pre>
        <pre>U.S.: +1 (650) 307-6736<p></p></pre>
        <pre>India: +91 (79) 40 500 100<p></p></pre>
        <pre>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;<p></p></pre>
    </div>
</div>"""

MSOFFICE_1_IN = ['Our requirements are simple. Just looking to replace some spreadsheets for tracking quotes and possibly using the timecard module.']
MSOFFICE_1_OUT = ['I noticed you recently downloaded OpenERP.', 'Uou mentioned you wish to use OpenERP in your own company.', 'Belgium: +32.81.81.37.00']

MSOFFICE_2 = """
<div>
  <div class="WordSection1">
    <p class="MsoNormal">
      <span style="font-size:11.0pt;font-family:&quot;Calibri&quot;,&quot;sans-serif&quot;;color:#1F497D">Nicolas,</span></p><p></p>
    <p></p>
    <p class="MsoNormal" style="text-indent:.5in">
      <span style="font-size:11.0pt;font-family:&quot;Calibri&quot;,&quot;sans-serif&quot;;color:#1F497D">We are currently investigating the possibility of moving away from our current ERP </span></p><p></p>
    <p></p>
    <p class="MsoNormal">
      <span style="font-size:11.0pt;font-family:&quot;Calibri&quot;,&quot;sans-serif&quot;;color:#1F497D">
        </span></p><p>&nbsp;</p>
      
    <p></p>
    <p class="MsoNormal">
      <span style="font-size:11.0pt;font-family:&quot;Calibri&quot;,&quot;sans-serif&quot;;color:#1F497D">Thank You</span></p><p></p>
    <p></p>
    <p class="MsoNormal">
      <span style="font-size:11.0pt;font-family:&quot;Calibri&quot;,&quot;sans-serif&quot;;color:#1F497D">Matt</span></p><p></p>
    <p></p>
    <p class="MsoNormal">
      <span style="font-size:11.0pt;font-family:&quot;Calibri&quot;,&quot;sans-serif&quot;;color:#1F497D">
        </span></p><p>&nbsp;</p>
      
    <p></p>
    <div>
      <p class="MsoNormal">
        <span style="font-size:11.0pt;font-family:&quot;Calibri&quot;,&quot;sans-serif&quot;;color:#1F497D">Raoul Petitpoil</span></p><p></p>
      <p></p>
      <p class="MsoNormal">
        <span style="font-size:11.0pt;font-family:&quot;Calibri&quot;,&quot;sans-serif&quot;;color:#1F497D">Poil Industries</span></p><p></p>
      <p></p>
      <p class="MsoNormal">
        <span style="font-size:11.0pt;font-family:&quot;Calibri&quot;,&quot;sans-serif&quot;;color:#1F497D">Information Technology</span></p><p></p>
      <p></p>
      <p class="MsoNormal">
        <span style="font-size:11.0pt;font-family:&quot;Calibri&quot;,&quot;sans-serif&quot;;color:#1F497D">920 Super Street</span></p><p></p>
      <p></p>
      <p class="MsoNormal">
        <span style="font-size:11.0pt;font-family:&quot;Calibri&quot;,&quot;sans-serif&quot;;color:#1F497D">Sanchez, Pa 17046 USA</span></p><p></p>
      <p></p>
      <p class="MsoNormal">
        <span style="font-size:11.0pt;font-family:&quot;Calibri&quot;,&quot;sans-serif&quot;;color:#1F497D">Tel: xxx.xxx</span></p><p></p>
      <p></p>
      <p class="MsoNormal">
        <span style="font-size:11.0pt;font-family:&quot;Calibri&quot;,&quot;sans-serif&quot;;color:#1F497D">Fax: xxx.xxx</span></p><p></p>
      <p></p>
      <p class="MsoNormal">
        <span style="font-size:11.0pt;font-family:&quot;Calibri&quot;,&quot;sans-serif&quot;;color:#1F497D">Email: </span>
        <a href="mailto:raoul@petitpoil.com">
          <span style="font-size:11.0pt;font-family:&quot;Calibri&quot;,&quot;sans-serif&quot;;color:blue">raoul@petitpoil.com</span>
        </a>
        <span style="font-size:11.0pt;font-family:&quot;Calibri&quot;,&quot;sans-serif&quot;;color:#1F497D">
          </span></p><p></p>
        
      <p></p>
      <p class="MsoNormal">
        <span style="font-size:11.0pt;font-family:&quot;Calibri&quot;,&quot;sans-serif&quot;;color:#1F497D">www.poilindustries.com</span></p><p></p>
      <p></p>
      <p class="MsoNormal">
        <span style="font-size:11.0pt;font-family:&quot;Calibri&quot;,&quot;sans-serif&quot;;color:#1F497D">www.superproducts.com</span></p><p></p>
      <p></p>
    </div>
    <p class="MsoNormal">
      <span style="font-size:11.0pt;font-family:&quot;Calibri&quot;,&quot;sans-serif&quot;;color:#1F497D">
        </span></p><p>&nbsp;</p>
      
    <p></p>
    <div>
      <div style="border:none;border-top:solid #B5C4DF 1.0pt;padding:3.0pt 0in 0in 0in">
        <p class="MsoNormal">
          <b>
            <span style="font-size:10.0pt;font-family:&quot;Tahoma&quot;,&quot;sans-serif&quot;">From:</span>
          </b>
          <span style="font-size:10.0pt;font-family:&quot;Tahoma&quot;,&quot;sans-serif&quot;"> OpenERP Enterprise [mailto:sales@openerp.com] <br><b>Sent:</b> Wednesday, April 17, 2013 1:31 PM<br><b>To:</b> Matt Witters<br><b>Subject:</b> Re: your OpenERP.com registration</span></p><p></p>
        <p></p>
      </div>
    </div>
    <p class="MsoNormal"></p>
    <p>&nbsp;</p>
    <p>Hello Raoul Petitpoil, </p>
    <p></p>
    <p>I noticed you recently downloaded OpenERP. </p>
    <p></p>
    <p>You indicated that you wish to use OpenERP in your own company. We would like to know more about your your business needs and requirements, and see how we can help you. When would you be available to discuss your project ? </p>
    <p></p>
    <p>Best regards, </p>
    <p></p>
    <pre>      <p>&nbsp;</p>
    </pre>
    <pre>--<p></p></pre>
    <pre>Nicolas<p></p></pre>
    <pre>      <a href="http://openerp.com">http://openerp.com</a>
      <p></p>
    </pre>
    <pre>Belgium: +32.81.81.37.00<p></p></pre>
    <pre>U.S.: +1 (650) 307-6736<p></p></pre>
    <pre>India: +91 (79) 40 500 100<p></p></pre>
    <pre>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; <p></p></pre>
  </div>
</div>"""

MSOFFICE_2_IN = ['We are currently investigating the possibility']
MSOFFICE_2_OUT = ['I noticed you recently downloaded OpenERP.', 'You indicated that you wish', 'Belgium: +32.81.81.37.00']

MSOFFICE_3 = """<div>
  <div class="WordSection1">
    <p class="MsoNormal">
      <span style="font-size:11.0pt;font-family:&quot;Calibri&quot;,&quot;sans-serif&quot;;color:#1F497D">Hi Nicolas&nbsp;!</span></p><p></p>
    <p></p>
    <p class="MsoNormal">
      <span style="font-size:11.0pt;font-family:&quot;Calibri&quot;,&quot;sans-serif&quot;;color:#1F497D">
        </span></p><p>&nbsp;</p>
      
    <p></p>
    <p class="MsoNormal">
      <span lang="EN-US" style="font-size:11.0pt;font-family:&quot;Calibri&quot;,&quot;sans-serif&quot;;color:#1F497D">Yes I’d be glad to hear about your offers as we struggle every year with the planning/approving of LOA. </span></p><p></p>
    <p></p>
    <p class="MsoNormal">
      <span lang="EN-US" style="font-size:11.0pt;font-family:&quot;Calibri&quot;,&quot;sans-serif&quot;;color:#1F497D">I saw your boss yesterday on tv and immediately wanted to test the interface. </span></p><p></p>
    <p></p>
    <p class="MsoNormal">
      <span lang="EN-US" style="font-size:11.0pt;font-family:&quot;Calibri&quot;,&quot;sans-serif&quot;;color:#1F497D">
        </span></p><p>&nbsp;</p>

    <p></p>
    <div>
      <p class="MsoNormal">
        <b>
          <span lang="NL-BE" style="font-size:10.0pt;font-family:&quot;Trebuchet MS&quot;,&quot;sans-serif&quot;;color:gray">Bien à vous, </span></b></p><p></p><b>
        </b>
      <p></p>
      <p class="MsoNormal">
        <b>
          <span lang="NL-BE" style="font-size:10.0pt;font-family:&quot;Trebuchet MS&quot;,&quot;sans-serif&quot;;color:gray">Met vriendelijke groeten, </span></b></p><p></p><b>
        </b>
      <p></p>
      <p class="MsoNormal">
        <b>
          <span lang="EN-GB" style="font-size:10.0pt;font-family:&quot;Trebuchet MS&quot;,&quot;sans-serif&quot;;color:gray">Best regards,</span></b></p><p></p><b>
        </b>
      <p></p>
      <p class="MsoNormal">
        <b>
          <span lang="EN-GB" style="font-size:10.0pt;font-family:&quot;Trebuchet MS&quot;,&quot;sans-serif&quot;;color:gray">
            </span></b></p><p><b>&nbsp;</b></p><b>
          
        </b>
      <p></p>
      <p class="MsoNormal">
        <b>
          <span lang="EN-GB" style="font-size:10.0pt;font-family:&quot;Trebuchet MS&quot;,&quot;sans-serif&quot;;color:gray">R. Petitpoil&nbsp;&nbsp;&nbsp; <br></span>
        </b>
        <span lang="EN-GB" style="font-size:10.0pt;font-family:&quot;Trebuchet MS&quot;,&quot;sans-serif&quot;;color:gray">Human Resource Manager<b><br><br>Field Resource s.a n.v.&nbsp;&nbsp;<i> <br></i></b>Hermesstraat 6A <br>1930 Zaventem</span>
        <span lang="EN-GB" style="font-size:8.0pt;font-family:&quot;Tahoma&quot;,&quot;sans-serif&quot;;color:gray"><br></span>
        <b>
          <span lang="FR" style="font-size:10.0pt;font-family:Wingdings;color:#1F497D">(</span>
        </b>
        <b>
          <span lang="FR" style="font-size:9.0pt;font-family:Wingdings;color:#1F497D"> </span>
        </b>
        <b>
          <span lang="EN-GB" style="font-size:8.0pt;font-family:&quot;Trebuchet MS&quot;,&quot;sans-serif&quot;;color:gray">xxx.xxx &nbsp;</span>
        </b>
        <b>
          <span lang="EN-GB" style="font-size:9.0pt;font-family:&quot;Trebuchet MS&quot;,&quot;sans-serif&quot;;color:gray"><br></span>
        </b>
        <b>
          <span lang="FR" style="font-size:10.0pt;font-family:&quot;Wingdings 2&quot;;color:#1F497D">7</span>
        </b>
        <b>
          <span lang="FR" style="font-size:9.0pt;font-family:&quot;Wingdings 2&quot;;color:#1F497D"> </span>
        </b>
        <b>
          <span lang="EN-GB" style="font-size:8.0pt;font-family:&quot;Trebuchet MS&quot;,&quot;sans-serif&quot;;color:gray">+32 2 727.05.91<br></span>
        </b>
        <span lang="EN-GB" style="font-size:24.0pt;font-family:Webdings;color:green">P</span>
        <span lang="EN-GB" style="font-size:8.0pt;font-family:&quot;Tahoma&quot;,&quot;sans-serif&quot;;color:green"> <b>&nbsp;&nbsp; </b></span>
        <b>
          <span lang="EN-GB" style="font-size:9.0pt;font-family:&quot;Trebuchet MS&quot;,&quot;sans-serif&quot;;color:green">Please consider the environment before printing this email.</span>
        </b>
        <span lang="EN-GB" style="font-size:11.0pt;font-family:&quot;Calibri&quot;,&quot;sans-serif&quot;;color:navy"> </span>
        <span lang="EN-GB" style="font-family:&quot;Calibri&quot;,&quot;sans-serif&quot;;color:navy">
          </span></p><p></p>
        
      <p></p>
    </div>
    <p class="MsoNormal">
      <span lang="EN-US" style="font-size:11.0pt;font-family:&quot;Calibri&quot;,&quot;sans-serif&quot;;color:#1F497D">
        </span></p><p>&nbsp;</p>
      
    <p></p>
    <div>
      <div style="border:none;border-top:solid #B5C4DF 1.0pt;padding:3.0pt 0cm 0cm 0cm">
        <p class="MsoNormal">
          <b>
            <span lang="FR" style="font-size:10.0pt;font-family:&quot;Tahoma&quot;,&quot;sans-serif&quot;">De&nbsp;:</span>
          </b>
          <span lang="FR" style="font-size:10.0pt;font-family:&quot;Tahoma&quot;,&quot;sans-serif&quot;"> OpenERP Enterprise [mailto:sales@openerp.com] <br><b>Envoyé&nbsp;:</b> jeudi 18 avril 2013 11:31<br><b>À&nbsp;:</b> Paul Richard<br><b>Objet&nbsp;:</b> Re: your OpenERP.com registration</span></p><p></p>
        <p></p>
      </div>
    </div>
    <p class="MsoNormal"></p>
    <p>&nbsp;</p>
    <p>Hello Raoul PETITPOIL, </p>
    <p></p>
    <p>I noticed you recently registered to our OpenERP Online solution. </p>
    <p></p>
    <p>You indicated that you wish to use OpenERP in your own company. We would like to know more about your your business needs and requirements, and see how we can help you. When would you be available to discuss your project ? </p>
    <p></p>
    <p>Best regards, </p>
    <p></p>
    <pre>      <p>&nbsp;</p>
    </pre>
    <pre>--<p></p></pre>
    <pre>Nicolas<p></p></pre>
    <pre>      <a href="http://openerp.com">http://openerp.com</a>
      <p></p>
    </pre>
    <pre>Belgium: +32.81.81.37.00<p></p></pre>
    <pre>U.S.: +1 (650) 307-6736<p></p></pre>
    <pre>India: +91 (79) 40 500 100<p></p></pre>
    <pre>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; <p></p></pre>
  </div>
</div>"""

MSOFFICE_3_IN = ['I saw your boss yesterday']
MSOFFICE_3_OUT = ['I noticed you recently downloaded OpenERP.', 'You indicated that you wish', 'Belgium: +32.81.81.37.00']
